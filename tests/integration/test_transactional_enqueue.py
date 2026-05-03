import pytest

from soniq import Soniq
from tests.db_utils import TEST_DATABASE_URL


@pytest.mark.asyncio
async def test_transactional_enqueue_rolls_back():
    app = Soniq(database_url=TEST_DATABASE_URL)

    @app.job(name="txn_job")
    async def txn_job():
        return "ok"

    pool = await app._get_pool()
    async with pool.acquire() as conn:
        job_id = None
        try:
            async with conn.transaction():
                job_id = await app.enqueue("txn_job", connection=conn)
                raise RuntimeError("rollback")
        except RuntimeError:
            pass

        row = await conn.fetchrow("SELECT id FROM soniq_jobs WHERE id = $1", job_id)
        assert row is None

    await app.close()


@pytest.mark.asyncio
async def test_transactional_enqueue_commits():
    app = Soniq(database_url=TEST_DATABASE_URL)

    @app.job(name="txn_job_commit")
    async def txn_job_commit():
        return "ok"

    pool = await app._get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            job_id = await app.enqueue("txn_job_commit", connection=conn)

        row = await conn.fetchrow("SELECT id FROM soniq_jobs WHERE id = $1", job_id)
        assert row is not None

    await app.close()


@pytest.mark.asyncio
async def test_transactional_schedule_rolls_back():
    app = Soniq(database_url=TEST_DATABASE_URL)

    @app.job(name="txn_scheduled_job")
    async def txn_scheduled_job():
        return "scheduled"

    pool = await app._get_pool()
    async with pool.acquire() as conn:
        job_id = None
        try:
            async with conn.transaction():
                job_id = await app.schedule(
                    "txn_scheduled_job", run_at=60, connection=conn
                )
                raise RuntimeError("rollback")
        except RuntimeError:
            pass

        row = await conn.fetchrow("SELECT id FROM soniq_jobs WHERE id = $1", job_id)
        assert row is None

    await app.close()


@pytest.mark.asyncio
async def test_non_transactional_enqueue_commits_immediately():
    app = Soniq(database_url=TEST_DATABASE_URL)

    @app.job(name="non_txn_job")
    async def non_txn_job():
        return "ok"

    job_id = await app.enqueue("non_txn_job")

    pool = await app._get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id FROM soniq_jobs WHERE id = $1", job_id)
        assert row is not None

    await app.close()


@pytest.mark.asyncio
async def test_unique_enqueue_rollback_does_not_block_next_enqueue():
    import uuid

    app = Soniq(database_url=TEST_DATABASE_URL)

    @app.job(name="unique_job", unique=True)
    async def unique_job(payload: str):
        return payload

    unique_payload = str(uuid.uuid4())

    pool = await app._get_pool()
    async with pool.acquire() as conn:
        try:
            async with conn.transaction():
                await app.enqueue(
                    "unique_job", args={"payload": unique_payload}, connection=conn
                )
                raise RuntimeError("rollback")
        except RuntimeError:
            pass

    job_id = await app.enqueue("unique_job", args={"payload": unique_payload})
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id FROM soniq_jobs WHERE id = $1", job_id)
        assert row is not None

    await app.close()
