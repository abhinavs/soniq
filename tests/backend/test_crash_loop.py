"""
Backend conformance: crash-loop must not retry forever.

When a worker crashes between fetch_and_lock_job and mark_job_failed,
cleanup_stale_workers resets the job to queued. The attempt counter
must have been incremented by fetch_and_lock_job so the job eventually
reaches max_attempts and gets dead-lettered.
"""


async def test_fetch_increments_attempts(backend):
    """fetch_and_lock_job must increment attempts atomically."""
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

    record = await backend.fetch_and_lock_job(queues=["default"], worker_id=None)
    assert record["attempts"] == 1, "First fetch should set attempts to 1"


async def test_crash_loop_increments_attempts(backend):
    """Repeated crashes must increment attempts, not leave them at 0."""
    await backend.register_worker(
        worker_id="w1", hostname="h", pid=1, queues=["default"], concurrency=1
    )
    await backend.create_job(
        job_id="j1",
        job_name="mod.func",
        args={},
        args_hash=None,
        max_attempts=2,
        priority=100,
        queue="default",
        unique=False,
    )

    # Crash 1: fetch (attempts -> 1), then crash (cleanup resets to queued)
    record = await backend.fetch_and_lock_job(queues=["default"], worker_id="w1")
    assert record["attempts"] == 1
    await backend.cleanup_stale_workers(stale_threshold_seconds=0)

    job = await backend.get_job("j1")
    assert job["status"] == "queued"
    assert job["attempts"] == 1, "Attempts must not reset to 0 on crash"

    # Crash 2: fetch (attempts -> 2), then crash
    await backend.register_worker(
        worker_id="w2", hostname="h", pid=2, queues=["default"], concurrency=1
    )
    record = await backend.fetch_and_lock_job(queues=["default"], worker_id="w2")
    assert record["attempts"] == 2
    await backend.cleanup_stale_workers(stale_threshold_seconds=0)

    job = await backend.get_job("j1")
    assert job["status"] == "queued"
    assert job["attempts"] == 2
