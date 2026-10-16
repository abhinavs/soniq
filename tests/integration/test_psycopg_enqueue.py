"""Transactional enqueue with a psycopg 3 connection, direct and through SQLAlchemy."""

import pytest

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("sqlalchemy")

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from soniq import Soniq  # noqa: E402
from tests.db_utils import TEST_DATABASE_URL  # noqa: E402

SA_URL = TEST_DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)


@pytest.fixture
async def soniq():
    app = Soniq(database_url=TEST_DATABASE_URL)

    @app.job(name="pg3_job")
    async def pg3_job(n: int):
        return None

    await app.ensure_initialized()
    yield app
    await app.close()


async def test_psycopg_commit(soniq):
    async with await psycopg.AsyncConnection.connect(TEST_DATABASE_URL) as conn:
        job_id = await soniq.enqueue("pg3_job", connection=conn, args={"n": 1})
        await conn.commit()
    assert (await soniq.get_job(job_id))["args"] == {"n": 1}


async def test_psycopg_rollback(soniq):
    async with await psycopg.AsyncConnection.connect(TEST_DATABASE_URL) as conn:
        job_id = await soniq.enqueue("pg3_job", connection=conn, args={"n": 2})
        await conn.rollback()
    assert await soniq.get_job(job_id) is None


async def test_psycopg_unique_returns_existing_id(soniq):
    async with await psycopg.AsyncConnection.connect(TEST_DATABASE_URL) as conn:
        first = await soniq.enqueue(
            "pg3_job", connection=conn, args={"n": 3}, unique=True
        )
        second = await soniq.enqueue(
            "pg3_job", connection=conn, args={"n": 3}, unique=True
        )
        await conn.commit()
    assert first == second


async def test_psycopg_dedup_key_returns_existing_id(soniq):
    async with await psycopg.AsyncConnection.connect(TEST_DATABASE_URL) as conn:
        first = await soniq.enqueue(
            "pg3_job", connection=conn, args={"n": 4}, dedup_key="pg3-k"
        )
        second = await soniq.enqueue(
            "pg3_job", connection=conn, args={"n": 5}, dedup_key="pg3-k"
        )
        await conn.commit()
    assert first == second
    assert (await soniq.get_job(first))["args"] == {"n": 4}


async def test_sqlalchemy_psycopg_commit_and_rollback(soniq):
    engine = create_async_engine(SA_URL)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
            ok = await soniq.enqueue("pg3_job", connection=conn, args={"n": 6})
        with pytest.raises(RuntimeError):
            async with engine.begin() as conn:
                gone = await soniq.enqueue("pg3_job", connection=conn, args={"n": 7})
                raise RuntimeError("boom")
    finally:
        await engine.dispose()
    assert (await soniq.get_job(ok))["args"] == {"n": 6}
    assert await soniq.get_job(gone) is None
