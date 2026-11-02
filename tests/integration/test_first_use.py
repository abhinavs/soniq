"""What happens on the very first enqueue of a fresh Soniq instance."""

import asyncio
import uuid

import asyncpg
import pytest

from soniq import Soniq, SoniqError
from tests.db_utils import TEST_DATABASE_URL, make_test_db_url, run_createdb, run_dropdb


async def test_concurrent_first_enqueues_share_one_pool(monkeypatch):
    # Before the fix each of these opened its own pool and all but one leaked.
    created = []
    real_create_pool = asyncpg.create_pool

    async def counting_create_pool(*args, **kwargs):
        created.append(1)
        return await real_create_pool(*args, **kwargs)

    monkeypatch.setattr(asyncpg, "create_pool", counting_create_pool)
    app = Soniq(
        database_url=TEST_DATABASE_URL,
        pool_min_size=1,
        pool_max_size=3,
        enqueue_validation="none",
    )
    try:
        await asyncio.gather(
            *(app.enqueue("first_use.noop", args={}) for _ in range(30))
        )
    finally:
        await app.close()
    assert len(created) == 1


async def test_enqueue_before_setup_says_to_run_setup():
    name = f"soniq_noschema_{uuid.uuid4().hex[:8]}"
    if run_createdb(name).returncode != 0:
        pytest.skip("createdb is not available")
    url = make_test_db_url(name)
    app = Soniq(database_url=url, enqueue_validation="none")
    try:
        with pytest.raises(SoniqError) as info:
            await app.enqueue("first_use.noop", args={})
        assert info.value.error_code == "SONIQ_SCHEMA_MISSING"

        # Transactional enqueue on a raw asyncpg connection.
        async with app.backend.acquire() as conn:
            with pytest.raises(SoniqError) as info:
                async with conn.transaction():
                    await app.enqueue("first_use.noop", args={}, connection=conn)
            assert info.value.error_code == "SONIQ_SCHEMA_MISSING"

        # Same on a psycopg connection, which raises its own error class.
        psycopg = pytest.importorskip("psycopg")
        async with await psycopg.AsyncConnection.connect(url) as pconn:
            with pytest.raises(SoniqError) as info:
                async with pconn.transaction():
                    await app.enqueue("first_use.noop", args={}, connection=pconn)
            assert info.value.error_code == "SONIQ_SCHEMA_MISSING"
    finally:
        await app.close()
        run_dropdb(name)
