"""
Integration tests for dedup_key — custom deduplication key.
"""

import uuid

import pytest

from soniq.backends.postgres import PostgresBackend
from tests.db_utils import TEST_DATABASE_URL


@pytest.fixture
async def backend():
    b = PostgresBackend(database_url=TEST_DATABASE_URL)
    await b.initialize()
    yield b
    await b.reset()
    await b.close()


@pytest.mark.asyncio
async def test_dedup_key_prevents_duplicate(backend):
    """Two enqueues with same dedup_key should return same ID."""
    id1 = await backend.create_job(
        job_id=str(uuid.uuid4()),
        job_name="test.locked_job",
        args={"a": 1},
        args_hash=None,
        max_attempts=3,
        priority=100,
        queue="default",
        unique=False,
        dedup_key="lock:user:42",
        scheduled_at=None,
    )

    id2 = await backend.create_job(
        job_id=str(uuid.uuid4()),
        job_name="test.locked_job",
        args={"a": 2},  # Different args, same lock
        args_hash=None,
        max_attempts=3,
        priority=100,
        queue="default",
        unique=False,
        dedup_key="lock:user:42",
        scheduled_at=None,
    )

    assert id1 == id2


@pytest.mark.asyncio
async def test_dedup_key_allows_after_completion(backend):
    """After a locked job completes, same lock can be re-enqueued."""
    id1 = await backend.create_job(
        job_id=str(uuid.uuid4()),
        job_name="test.locked_job",
        args={},
        args_hash=None,
        max_attempts=3,
        priority=100,
        queue="default",
        unique=False,
        dedup_key="lock:report:daily",
        scheduled_at=None,
    )

    # Complete the job
    await backend.fetch_and_lock_job(queues=["default"], worker_id=None)
    await backend.mark_job_done(id1, result_ttl=0)

    # Same lock should now be re-enqueued
    id2 = await backend.create_job(
        job_id=str(uuid.uuid4()),
        job_name="test.locked_job",
        args={},
        args_hash=None,
        max_attempts=3,
        priority=100,
        queue="default",
        unique=False,
        dedup_key="lock:report:daily",
        scheduled_at=None,
    )

    assert id1 != id2  # New job, different ID


@pytest.mark.asyncio
async def test_no_lock_allows_duplicates(backend):
    """Without dedup_key, duplicates are allowed."""
    id1 = await backend.create_job(
        job_id=str(uuid.uuid4()),
        job_name="test.unlocked",
        args={},
        args_hash=None,
        max_attempts=3,
        priority=100,
        queue="default",
        unique=False,
        dedup_key=None,
        scheduled_at=None,
    )

    id2 = await backend.create_job(
        job_id=str(uuid.uuid4()),
        job_name="test.unlocked",
        args={},
        args_hash=None,
        max_attempts=3,
        priority=100,
        queue="default",
        unique=False,
        dedup_key=None,
        scheduled_at=None,
    )

    assert id1 != id2  # Two different jobs
