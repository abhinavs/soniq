"""
Core job lifecycle tests using MemoryBackend.

These prove the full enqueue → process → done/retry/dead-letter flow
works with zero external dependencies.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from soniq.core.processor import process_job_via_backend
from soniq.core.registry import JobRegistry
from soniq.testing.memory_backend import MemoryBackend


@pytest.fixture
def backend():
    return MemoryBackend()


@pytest.fixture
def registry():
    return JobRegistry()


async def _enqueue(backend, registry, func, args=None, **overrides):
    """Helper to register a job and create it in the backend."""
    job_name = func.__name__
    registry.register_job(
        func,
        name=job_name,
        **{
            k: v
            for k, v in overrides.items()
            if k in ("max_retries", "queue", "priority", "timeout")
        },
    )
    job_id = str(uuid.uuid4())

    args_dict = args or {}
    job_meta = registry.get_job(job_name)
    max_attempts = (
        overrides.get("max_retries", job_meta["max_retries"]) if job_meta else 3
    ) + 1

    await backend.create_job(
        job_id=job_id,
        job_name=job_name,
        args=args_dict,
        args_hash=None,
        max_attempts=max_attempts,
        priority=overrides.get("priority", 100),
        queue=overrides.get("queue", "default"),
        unique=False,
        dedup_key=None,
        scheduled_at=overrides.get("scheduled_at"),
    )
    return job_id


@pytest.mark.asyncio
async def test_enqueue_creates_queued_job(backend, registry):
    async def my_job():
        pass

    job_id = await _enqueue(backend, registry, my_job)
    job = await backend.get_job(job_id)
    assert job["status"] == "queued"


@pytest.mark.asyncio
async def test_process_marks_job_done(backend, registry):
    executed = []

    async def my_job(x: int):
        executed.append(x)

    job_id = await _enqueue(backend, registry, my_job, {"x": 42})
    await process_job_via_backend(
        backend=backend, job_registry=registry, queues=["default"]
    )

    assert executed == [42]
    job = await backend.get_job(job_id)
    assert job["status"] == "done"


@pytest.mark.asyncio
async def test_failed_job_retries(backend, registry):
    async def failing_job():
        raise RuntimeError("temporary failure")

    job_id = await _enqueue(backend, registry, failing_job, retries=3)
    await process_job_via_backend(
        backend=backend, job_registry=registry, queues=["default"]
    )

    job = await backend.get_job(job_id)
    assert job["status"] == "queued"
    assert job["attempts"] == 1


@pytest.mark.asyncio
async def test_failed_job_dead_letters_after_max_attempts(backend, registry):
    async def always_fails():
        raise RuntimeError("permanent")

    job_id = await _enqueue(backend, registry, always_fails, max_retries=1)

    # Attempt 1 → retry
    await process_job_via_backend(
        backend=backend, job_registry=registry, queues=["default"]
    )
    assert (await backend.get_job(job_id))["status"] == "queued"

    # Attempt 2 -> dead letter. DLQ Option A: row leaves soniq_jobs.
    await process_job_via_backend(
        backend=backend, job_registry=registry, queues=["default"]
    )
    assert await backend.get_job(job_id) is None
    assert job_id in backend._dead_letter_jobs


@pytest.mark.asyncio
async def test_cancel_queued_job(backend, registry):
    async def my_job():
        pass

    job_id = await _enqueue(backend, registry, my_job)
    result = await backend.cancel_job(job_id)
    assert result is True
    assert (await backend.get_job(job_id))["status"] == "cancelled"


@pytest.mark.asyncio
async def test_dead_lettered_job_removed_from_soniq_jobs(backend, registry):
    """DLQ Option A: dead-lettered jobs are gone from ``soniq_jobs`` and
    live exclusively in the dead-letter table. Resurrection happens via
    ``DeadLetterService.replay`` (postgres-only) or by re-enqueuing."""

    async def always_fails():
        raise RuntimeError("boom")

    job_id = await _enqueue(backend, registry, always_fails, max_retries=0)
    await process_job_via_backend(
        backend=backend, job_registry=registry, queues=["default"]
    )
    assert await backend.get_job(job_id) is None
    assert job_id in backend._dead_letter_jobs


@pytest.mark.asyncio
async def test_scheduled_job_not_picked_up_early(backend, registry):
    async def my_job():
        pass

    future = datetime.now(timezone.utc) + timedelta(hours=1)
    job_id = await _enqueue(backend, registry, my_job, scheduled_at=future)

    result = await process_job_via_backend(
        backend=backend, job_registry=registry, queues=["default"]
    )
    assert result is False  # Not picked up — scheduled in the future
    assert (await backend.get_job(job_id))["status"] == "queued"


@pytest.mark.asyncio
async def test_unique_job_deduplication(backend, registry):
    async def my_job():
        pass

    job_name = my_job.__name__
    registry.register_job(my_job, name=job_name, unique=True)
    from soniq.utils.hashing import compute_args_hash

    args_hash = compute_args_hash({"key": "value"})

    id1 = await backend.create_job(
        job_id=str(uuid.uuid4()),
        job_name=job_name,
        args={"key": "value"},
        args_hash=args_hash,
        max_attempts=3,
        priority=100,
        queue="default",
        unique=True,
        dedup_key=None,
        scheduled_at=None,
    )

    id2 = await backend.create_job(
        job_id=str(uuid.uuid4()),
        job_name=job_name,
        args={"key": "value"},
        args_hash=args_hash,
        max_attempts=3,
        priority=100,
        queue="default",
        unique=True,
        dedup_key=None,
        scheduled_at=None,
    )

    assert id1 == id2  # Second enqueue returns existing ID


@pytest.mark.asyncio
async def test_priority_ordering(backend, registry):
    executed = []

    async def task(name: str):
        executed.append(name)

    job_name = task.__name__
    registry.register_job(task, name=job_name)

    # Enqueue low priority first, high priority second
    await backend.create_job(
        job_id="low",
        job_name=job_name,
        args={"name": "low"},
        args_hash=None,
        max_attempts=3,
        priority=100,
        queue="default",
        unique=False,
        dedup_key=None,
        scheduled_at=None,
    )
    await backend.create_job(
        job_id="high",
        job_name=job_name,
        args={"name": "high"},
        args_hash=None,
        max_attempts=3,
        priority=1,
        queue="default",
        unique=False,
        dedup_key=None,
        scheduled_at=None,
    )

    # Process — should pick high priority first
    await process_job_via_backend(
        backend=backend, job_registry=registry, queues=["default"]
    )
    await process_job_via_backend(
        backend=backend, job_registry=registry, queues=["default"]
    )

    assert executed == ["high", "low"]
