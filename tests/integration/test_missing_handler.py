"""
Test that jobs with unregistered handlers are dead-lettered correctly.
"""

import uuid

import pytest

from soniq import Soniq
from soniq.core.registry import JobRegistry
from soniq.core.worker import Worker
from tests.db_utils import TEST_DATABASE_URL


@pytest.mark.asyncio
async def test_unregistered_job_dead_lettered():
    """
    A job with a job_name that doesn't exist in the registry
    should be moved to dead_letter with a clear error message.
    """
    app = Soniq(database_url=TEST_DATABASE_URL)
    pool = await app._get_pool()

    job_id = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO soniq_jobs (id, job_name, args, status, max_attempts, queue)
            VALUES ($1, 'nonexistent.module.fake_function', '{}', 'queued', 3, 'default')
            """,
            job_id,
        )

    empty_registry = JobRegistry()
    backend = app._backend
    worker = Worker(backend, empty_registry)
    processed = await worker.run_once(queues=None, max_jobs=1)

    assert processed is True

    async with pool.acquire() as conn:
        in_jobs = await conn.fetchrow(
            "SELECT id FROM soniq_jobs WHERE id = $1",
            job_id,
        )
        assert in_jobs is None
        row = await conn.fetchrow(
            "SELECT last_error FROM soniq_dead_letter_jobs WHERE id = $1",
            job_id,
        )

    assert row is not None
    assert "not registered" in row["last_error"].lower()

    await app.close()
