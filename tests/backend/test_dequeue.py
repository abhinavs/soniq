"""
Backend conformance: dequeue behavior (fetch_and_lock_job).
"""

from datetime import datetime, timedelta, timezone


async def test_empty_queue_returns_none(backend):
    result = await backend.fetch_and_lock_job(queues=["default"], worker_id=None)
    assert result is None


async def test_dequeue_returns_job(backend):
    await backend.create_job(
        job_id="j1",
        job_name="mod.func",
        args={"x": 1},
        args_hash=None,
        max_attempts=3,
        priority=100,
        queue="default",
        unique=False,
    )
    record = await backend.fetch_and_lock_job(queues=["default"], worker_id=None)
    assert record is not None
    assert str(record["id"]) == "j1"


async def test_no_double_dequeue(backend):
    """After fetch_and_lock, same job is not returned again."""
    await backend.create_job(
        job_id="j1",
        job_name="mod.func",
        args={},
        args_hash=None,
        max_attempts=3,
        priority=100,
        queue="default",
        unique=False,
    )
    await backend.fetch_and_lock_job(queues=["default"], worker_id=None)
    second = await backend.fetch_and_lock_job(queues=["default"], worker_id=None)
    assert second is None


async def test_priority_ordering(backend):
    """Lower priority number dequeued first."""
    await backend.create_job(
        job_id="low",
        job_name="mod.func",
        args={},
        args_hash=None,
        max_attempts=3,
        priority=100,
        queue="default",
        unique=False,
    )
    await backend.create_job(
        job_id="high",
        job_name="mod.func",
        args={},
        args_hash=None,
        max_attempts=3,
        priority=1,
        queue="default",
        unique=False,
    )
    record = await backend.fetch_and_lock_job(queues=["default"], worker_id=None)
    assert str(record["id"]) == "high"


async def test_scheduled_job_not_dequeued_early(backend):
    """Jobs scheduled in the future should not be dequeued."""
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    await backend.create_job(
        job_id="j1",
        job_name="mod.func",
        args={},
        args_hash=None,
        max_attempts=3,
        priority=100,
        queue="default",
        unique=False,
        scheduled_at=future,
    )
    result = await backend.fetch_and_lock_job(queues=["default"], worker_id=None)
    assert result is None


async def test_queue_filtering(backend):
    """Only jobs from requested queues are dequeued."""
    await backend.create_job(
        job_id="j1",
        job_name="mod.func",
        args={},
        args_hash=None,
        max_attempts=3,
        priority=100,
        queue="emails",
        unique=False,
    )
    await backend.create_job(
        job_id="j2",
        job_name="mod.func",
        args={},
        args_hash=None,
        max_attempts=3,
        priority=100,
        queue="billing",
        unique=False,
    )
    record = await backend.fetch_and_lock_job(queues=["emails"], worker_id=None)
    assert str(record["id"]) == "j1"
