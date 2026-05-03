"""
Integration tests for job timeout enforcement through the full processing pipeline.
"""

import asyncio

import pytest

from soniq import Soniq
from soniq.core.worker import Worker
from tests.db_utils import TEST_DATABASE_URL


@pytest.mark.asyncio
async def test_timed_out_job_retried_then_dead_lettered():
    """
    A job that always times out should be retried up to max_attempts
    and then moved to dead_letter.
    """
    app = Soniq(database_url=TEST_DATABASE_URL)

    @app.job(name="slow_timeout_job", retries=1, timeout=0.1)
    async def slow_timeout_job():
        """Job that always exceeds its per-job timeout."""
        await asyncio.sleep(5)

    await app.ensure_initialized()
    registry = app._get_job_registry()
    backend = app.backend
    worker = Worker(backend, registry)

    job_id = await app.enqueue("slow_timeout_job")

    processed = await worker.run_once(queues=None, max_jobs=1)
    assert processed is True

    status = await app.get_job(job_id)
    assert status["status"] == "queued"
    assert status["attempts"] == 1
    assert "timed out" in status["last_error"].lower()

    processed = await worker.run_once(queues=None, max_jobs=1)
    assert processed is True

    assert await app.get_job(job_id) is None
    pool = await app._get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT attempts FROM soniq_dead_letter_jobs WHERE id = $1",
            __import__("uuid").UUID(job_id),
        )
    assert row is not None
    assert row["attempts"] == 2

    await app.close()
