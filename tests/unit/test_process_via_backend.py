"""
Tests for the backend-based job processing path.

process_job_via_backend uses StorageBackend instead of raw asyncpg.Connection.
"""

import pytest

from soniq.core.registry import JobRegistry
from soniq.testing.memory_backend import MemoryBackend


@pytest.mark.asyncio
async def test_process_via_backend_exists():
    from soniq.core.processor import process_job_via_backend

    assert callable(process_job_via_backend)


@pytest.mark.asyncio
async def test_process_via_backend_runs_job():
    from soniq.core.processor import process_job_via_backend

    backend = MemoryBackend()
    await backend.initialize()
    registry = JobRegistry()

    executed = []

    async def my_task(msg: str):
        executed.append(msg)

    registry.register_job(my_task, name="my_task")
    job_name = "my_task"
    await backend.create_job(
        job_id="job-1",
        job_name=job_name,
        args={"msg": "hello"},
        args_hash=None,
        max_attempts=3,
        priority=100,
        queue="default",
        unique=False,
        dedup_key=None,
        scheduled_at=None,
    )

    result = await process_job_via_backend(
        backend=backend,
        job_registry=registry,
        queues=["default"],
    )

    assert result is True
    assert executed == ["hello"]

    job = await backend.get_job("job-1")
    assert job["status"] == "done"


@pytest.mark.asyncio
async def test_process_via_backend_returns_false_when_empty():
    from soniq.core.processor import process_job_via_backend

    backend = MemoryBackend()
    await backend.initialize()
    registry = JobRegistry()

    result = await process_job_via_backend(
        backend=backend,
        job_registry=registry,
        queues=["default"],
    )
    assert result is False


@pytest.mark.asyncio
async def test_process_via_backend_handles_failure_with_retry():
    from soniq.core.processor import process_job_via_backend

    backend = MemoryBackend()
    await backend.initialize()
    registry = JobRegistry()

    async def failing_task():
        raise RuntimeError("boom")

    registry.register_job(failing_task, name="failing_task")
    job_name = "failing_task"
    await backend.create_job(
        job_id="job-fail",
        job_name=job_name,
        args={},
        args_hash=None,
        max_attempts=3,
        priority=100,
        queue="default",
        unique=False,
        dedup_key=None,
        scheduled_at=None,
    )

    result = await process_job_via_backend(
        backend=backend,
        job_registry=registry,
        queues=["default"],
    )

    assert result is True
    job = await backend.get_job("job-fail")
    assert job["status"] == "queued"  # Retried, not dead-lettered (1 of 3 attempts)
    assert job["attempts"] == 1


@pytest.mark.asyncio
async def test_process_via_backend_dead_letters_after_max_attempts():
    from soniq.core.processor import process_job_via_backend

    backend = MemoryBackend()
    await backend.initialize()
    registry = JobRegistry()

    async def always_fails():
        raise RuntimeError("permanent")

    registry.register_job(always_fails, name="always_fails")
    job_name = "always_fails"
    await backend.create_job(
        job_id="job-dead",
        job_name=job_name,
        args={},
        args_hash=None,
        max_attempts=2,  # Only 2 attempts total
        priority=100,
        queue="default",
        unique=False,
        dedup_key=None,
        scheduled_at=None,
    )

    # First attempt
    await process_job_via_backend(
        backend=backend, job_registry=registry, queues=["default"]
    )
    job = await backend.get_job("job-dead")
    assert job["status"] == "queued"  # attempt 1, retried

    # Second attempt - should dead-letter (DLQ Option A: row moves out
    # of soniq_jobs into soniq_dead_letter_jobs in one transaction).
    await process_job_via_backend(
        backend=backend, job_registry=registry, queues=["default"]
    )
    assert await backend.get_job("job-dead") is None
    assert "job-dead" in backend._dead_letter_jobs
