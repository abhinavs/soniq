"""
Tests for MemoryBackend — the in-memory storage backend for unit tests.
"""

import uuid

import pytest


@pytest.mark.asyncio
async def test_memory_backend_importable():
    from soniq.testing.memory_backend import MemoryBackend

    assert MemoryBackend is not None


@pytest.mark.asyncio
async def test_memory_backend_satisfies_protocol():
    from soniq.backends import StorageBackend
    from soniq.testing.memory_backend import MemoryBackend

    backend = MemoryBackend()
    assert isinstance(backend, StorageBackend)


@pytest.mark.asyncio
async def test_create_and_get_job():
    from soniq.testing.memory_backend import MemoryBackend

    backend = MemoryBackend()
    await backend.initialize()

    job_id = str(uuid.uuid4())
    result = await backend.create_job(
        job_id=job_id,
        job_name="test.my_job",
        args={"x": 1},
        args_hash=None,
        max_attempts=3,
        priority=100,
        queue="default",
        unique=False,
        dedup_key=None,
        scheduled_at=None,
    )
    assert result == job_id

    job = await backend.get_job(job_id)
    assert job is not None
    assert job["id"] == job_id
    assert job["status"] == "queued"


@pytest.mark.asyncio
async def test_fetch_and_lock_job():
    from soniq.testing.memory_backend import MemoryBackend

    backend = MemoryBackend()
    await backend.initialize()

    job_id = str(uuid.uuid4())
    await backend.create_job(
        job_id=job_id,
        job_name="test.fetch",
        args={},
        args_hash=None,
        max_attempts=3,
        priority=100,
        queue="default",
        unique=False,
        dedup_key=None,
        scheduled_at=None,
    )

    locked = await backend.fetch_and_lock_job(queues=["default"], worker_id=None)
    assert locked is not None
    assert locked["id"] == job_id

    # Should be processing now
    job = await backend.get_job(job_id)
    assert job["status"] == "processing"

    # No more jobs
    empty = await backend.fetch_and_lock_job(queues=["default"], worker_id=None)
    assert empty is None


@pytest.mark.asyncio
async def test_mark_done_and_failed():
    from soniq.testing.memory_backend import MemoryBackend

    backend = MemoryBackend()
    await backend.initialize()

    # Done
    j1 = str(uuid.uuid4())
    await backend.create_job(
        job_id=j1,
        job_name="t",
        args={},
        args_hash=None,
        max_attempts=3,
        priority=100,
        queue="default",
        unique=False,
        dedup_key=None,
        scheduled_at=None,
    )
    await backend.fetch_and_lock_job(queues=["default"], worker_id=None)
    await backend.mark_job_done(j1, result_ttl=300)
    assert (await backend.get_job(j1))["status"] == "done"

    # Failed → back to queued
    j2 = str(uuid.uuid4())
    await backend.create_job(
        job_id=j2,
        job_name="t",
        args={},
        args_hash=None,
        max_attempts=3,
        priority=100,
        queue="default",
        unique=False,
        dedup_key=None,
        scheduled_at=None,
    )
    await backend.fetch_and_lock_job(queues=["default"], worker_id=None)
    await backend.mark_job_failed(j2, attempts=1, error="boom")
    assert (await backend.get_job(j2))["status"] == "queued"


@pytest.mark.asyncio
async def test_dead_letter():
    from soniq.testing.memory_backend import MemoryBackend

    backend = MemoryBackend()
    await backend.initialize()

    j = str(uuid.uuid4())
    await backend.create_job(
        job_id=j,
        job_name="t",
        args={},
        args_hash=None,
        max_attempts=3,
        priority=100,
        queue="default",
        unique=False,
        dedup_key=None,
        scheduled_at=None,
    )
    await backend.fetch_and_lock_job(queues=["default"], worker_id=None)
    await backend.mark_job_dead_letter(
        j,
        attempts=3,
        error="gave up",
        reason="max_retries_exceeded",
    )
    # DLQ Option A: row removed from soniq_jobs entirely.
    assert await backend.get_job(j) is None
    assert j in backend._dead_letter_jobs
    assert backend._dead_letter_jobs[j]["dead_letter_reason"] == "max_retries_exceeded"


@pytest.mark.asyncio
async def test_cancel_and_retry():
    from soniq.testing.memory_backend import MemoryBackend

    backend = MemoryBackend()
    await backend.initialize()

    j = str(uuid.uuid4())
    await backend.create_job(
        job_id=j,
        job_name="t",
        args={},
        args_hash=None,
        max_attempts=3,
        priority=100,
        queue="default",
        unique=False,
        dedup_key=None,
        scheduled_at=None,
    )
    assert await backend.cancel_job(j) is True
    assert (await backend.get_job(j))["status"] == "cancelled"

    # Can't cancel again
    assert await backend.cancel_job(j) is False


@pytest.mark.asyncio
async def test_reset():
    from soniq.testing.memory_backend import MemoryBackend

    backend = MemoryBackend()
    await backend.initialize()

    await backend.create_job(
        job_id=str(uuid.uuid4()),
        job_name="t",
        args={},
        args_hash=None,
        max_attempts=3,
        priority=100,
        queue="default",
        unique=False,
        dedup_key=None,
        scheduled_at=None,
    )

    jobs = await backend.list_jobs()
    assert len(jobs) == 1

    await backend.reset()
    jobs = await backend.list_jobs()
    assert len(jobs) == 0


@pytest.mark.asyncio
async def test_capabilities():
    from soniq.testing.memory_backend import MemoryBackend

    backend = MemoryBackend()
    assert backend.supports_push_notify is False


@pytest.mark.asyncio
async def test_result_persisted_and_retrieved_memory():
    """Memory backend persists handler return value through mark_job_done."""
    from soniq.testing.memory_backend import MemoryBackend

    backend = MemoryBackend()
    await backend.initialize()

    job_id = str(uuid.uuid4())
    await backend.create_job(
        job_id=job_id,
        job_name="test.result",
        args={},
        args_hash=None,
        max_attempts=3,
        priority=100,
        queue="default",
        unique=False,
        dedup_key=None,
        scheduled_at=None,
    )
    await backend.fetch_and_lock_job(queues=["default"], worker_id=None)
    await backend.mark_job_done(
        job_id, result_ttl=3600, result={"count": 7, "label": "done"}
    )

    job = await backend.get_job(job_id)
    assert job["status"] == "done"
    assert job["result"] == {"count": 7, "label": "done"}
    assert backend.supports_transactional_enqueue is False
