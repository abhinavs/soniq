"""
Backend conformance: worker registration, heartbeat, and stale cleanup.
"""


async def test_register_and_heartbeat(backend):
    """Basic worker registration and heartbeat should not error."""
    await backend.register_worker(
        worker_id="w1",
        hostname="host",
        pid=1234,
        queues=["default"],
        concurrency=4,
    )
    await backend.update_heartbeat("w1")
    await backend.mark_worker_stopped("w1")


async def test_cleanup_stale_requeues_jobs(backend):
    """Stale worker cleanup should requeue its processing jobs."""
    await backend.register_worker(
        worker_id="w1",
        hostname="host",
        pid=1234,
        queues=["default"],
        concurrency=4,
    )
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
    await backend.fetch_and_lock_job(queues=["default"], worker_id="w1")

    job = await backend.get_job("j1")
    assert job["status"] == "processing"

    # Cleanup with 0 threshold treats all workers as stale
    cleaned = await backend.cleanup_stale_workers(stale_threshold_seconds=0)
    assert cleaned >= 1

    job = await backend.get_job("j1")
    assert job["status"] == "queued"
