"""
Concurrent dequeue tests.

Proves FOR UPDATE SKIP LOCKED prevents duplicate job processing
under actual contention from multiple async tasks.
"""

import asyncio

import pytest

from soniq import Soniq
from tests.db_utils import TEST_DATABASE_URL


@pytest.mark.asyncio
async def test_concurrent_dequeue_no_duplicates():
    """
    Multiple async tasks racing to dequeue a single job:
    exactly one should succeed, the rest should get None.
    """
    app = Soniq(database_url=TEST_DATABASE_URL)

    @app.job(name="race_job", queue="race-test")
    async def race_job(n: int):
        pass

    await app.ensure_initialized()
    backend = app.backend

    await app.enqueue("race_job", args={"n": 1})

    async def try_dequeue():
        return await backend.fetch_and_lock_job(queues=["race-test"])

    results = await asyncio.gather(*[try_dequeue() for _ in range(5)])

    got_job = [r for r in results if r is not None]
    got_none = [r for r in results if r is None]

    assert len(got_job) == 1, f"Expected exactly 1 winner, got {len(got_job)}"
    assert len(got_none) == 4, f"Expected 4 losers, got {len(got_none)}"

    await app.close()


@pytest.mark.asyncio
async def test_concurrent_dequeue_distributes_jobs():
    """
    10 jobs, 5 concurrent workers: all 10 should be claimed with zero duplicates.
    """
    app = Soniq(database_url=TEST_DATABASE_URL)

    @app.job(name="race_job", queue="race-test")
    async def race_job(n: int):
        pass

    await app.ensure_initialized()
    backend = app.backend

    for i in range(10):
        await app.enqueue("race_job", args={"n": i})

    claimed_ids = []
    lock = asyncio.Lock()

    async def worker_loop():
        while True:
            job = await backend.fetch_and_lock_job(queues=["race-test"])
            if job is None:
                break
            async with lock:
                claimed_ids.append(job["id"])

    await asyncio.gather(*[worker_loop() for _ in range(5)])

    assert len(claimed_ids) == 10, f"Expected 10 jobs claimed, got {len(claimed_ids)}"
    assert len(set(claimed_ids)) == 10, "Duplicate job IDs found - race condition!"

    await app.close()


@pytest.mark.asyncio
async def test_unique_job_concurrent_enqueue():
    """
    10 concurrent enqueue calls with unique=True and same args:
    only 1 job should exist in the database.
    """
    app = Soniq(database_url=TEST_DATABASE_URL)

    @app.job(name="unique_race_job", queue="race-test", unique=True)
    async def unique_race_job(key: str):
        pass

    pool = await app._get_pool()

    async def try_enqueue():
        return await app.enqueue("unique_race_job", args={"key": "same-key"})

    results = await asyncio.gather(*[try_enqueue() for _ in range(10)])

    unique_ids = set(results)
    assert (
        len(unique_ids) == 1
    ), f"Expected all enqueues to return the same ID, got {len(unique_ids)} distinct IDs"

    async with pool.acquire() as conn:
        count = await conn.fetchval(
            """
            SELECT COUNT(*) FROM soniq_jobs
            WHERE job_name LIKE '%unique_race_job%' AND status = 'queued'
            """
        )
    assert count == 1, f"Expected 1 unique job row, found {count}"

    await app.close()
