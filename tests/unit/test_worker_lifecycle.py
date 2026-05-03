"""
Tests for Worker edge cases not covered by test_worker_class.py.

Covers: run_once with max_jobs, run_once on empty queue, _maybe_cleanup
interval gating and error handling, run() dispatching.
"""

import time
import uuid

import pytest

from soniq.core.registry import JobRegistry
from soniq.core.worker import Worker
from soniq.testing.memory_backend import MemoryBackend


async def _create_jobs(backend, registry, job_func, count, args_template=None):
    """Helper: register a job and create N instances in the queue."""
    job_name = job_func.__name__
    # Only register if not already registered
    if registry.get_job(job_name) is None:
        registry.register_job(job_func, name=job_func.__name__)
    for i in range(count):
        args = args_template or {"n": i}
        await backend.create_job(
            job_id=str(uuid.uuid4()),
            job_name=job_name,
            args=args,
            args_hash=None,
            max_attempts=3,
            priority=100,
            queue="default",
            unique=False,
            dedup_key=None,
            scheduled_at=None,
        )


@pytest.mark.asyncio
async def test_run_once_respects_max_jobs():
    """run_once(max_jobs=N) should process at most N jobs."""
    backend = MemoryBackend()
    await backend.initialize()
    registry = JobRegistry()
    executed = []

    async def track(n: int):
        executed.append(n)

    await _create_jobs(backend, registry, track, count=5)
    worker = Worker(backend=backend, registry=registry)
    result = await worker.run_once(queues=["default"], max_jobs=2)

    assert result is True
    assert len(executed) == 2


@pytest.mark.asyncio
async def test_run_once_returns_false_on_empty_queue():
    backend = MemoryBackend()
    await backend.initialize()
    registry = JobRegistry()
    worker = Worker(backend=backend, registry=registry)

    result = await worker.run_once(queues=["default"])
    assert result is False


@pytest.mark.asyncio
async def test_run_once_processes_all_when_no_max():
    backend = MemoryBackend()
    await backend.initialize()
    registry = JobRegistry()
    executed = []

    async def track(n: int):
        executed.append(n)

    await _create_jobs(backend, registry, track, count=3)
    worker = Worker(backend=backend, registry=registry)
    result = await worker.run_once(queues=["default"])

    assert result is True
    assert len(executed) == 3


@pytest.mark.asyncio
async def test_run_dispatches_to_run_once():
    """run(run_once=True) should delegate to run_once."""
    backend = MemoryBackend()
    await backend.initialize()
    registry = JobRegistry()
    executed = []

    async def my_task(n: int):
        executed.append(n)

    await _create_jobs(backend, registry, my_task, count=1)
    worker = Worker(backend=backend, registry=registry)
    result = await worker.run(run_once=True, queues=["default"])

    assert result is True
    assert len(executed) == 1


@pytest.mark.asyncio
async def test_maybe_cleanup_skips_when_interval_not_reached():
    """_maybe_cleanup should return early if cleanup_interval hasn't elapsed."""
    backend = MemoryBackend()
    await backend.initialize()
    registry = JobRegistry()
    worker = Worker(backend=backend, registry=registry)

    # Set last cleanup to now — cleanup should be skipped
    worker._last_cleanup = time.time()

    # MemoryBackend.delete_expired_jobs exists, track if it's called
    call_count = 0
    original = backend.delete_expired_jobs

    async def tracking_delete():
        nonlocal call_count
        call_count += 1
        return await original()

    backend.delete_expired_jobs = tracking_delete
    await worker._maybe_cleanup()
    assert call_count == 0  # Should have been skipped


@pytest.mark.asyncio
async def test_maybe_cleanup_runs_when_interval_elapsed():
    """_maybe_cleanup should run when enough time has passed."""
    backend = MemoryBackend()
    await backend.initialize()
    registry = JobRegistry()
    worker = Worker(backend=backend, registry=registry)

    # Set last cleanup far in the past
    worker._last_cleanup = 0.0

    call_count = 0
    original = backend.delete_expired_jobs

    async def tracking_delete():
        nonlocal call_count
        call_count += 1
        return await original()

    backend.delete_expired_jobs = tracking_delete
    await worker._maybe_cleanup()
    assert call_count == 1
    assert worker._last_cleanup > 0


@pytest.mark.asyncio
async def test_maybe_cleanup_handles_error_gracefully():
    """_maybe_cleanup should catch exceptions and still update timestamp."""
    backend = MemoryBackend()
    await backend.initialize()
    registry = JobRegistry()
    worker = Worker(backend=backend, registry=registry)
    worker._last_cleanup = 0.0

    async def exploding_delete():
        raise RuntimeError("cleanup failed")

    backend.delete_expired_jobs = exploding_delete
    # Should not raise
    await worker._maybe_cleanup()
    # Timestamp should still be updated
    assert worker._last_cleanup > 0


@pytest.mark.asyncio
async def test_worker_accepts_hooks():
    """Worker should pass hooks through to processor."""
    backend = MemoryBackend()
    await backend.initialize()
    registry = JobRegistry()
    hook_calls = []

    async def my_task(n: int):
        pass

    async def before(job_name, job_id, attempt):
        hook_calls.append("before")

    await _create_jobs(backend, registry, my_task, count=1)
    worker = Worker(backend=backend, registry=registry, hooks={"before_job": [before]})
    await worker.run_once(queues=["default"])
    assert hook_calls == ["before"]
