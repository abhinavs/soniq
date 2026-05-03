"""
Backend conformance: maintenance operations (reset, expired jobs).
"""


async def test_reset_clears_everything(backend):
    """reset() should clear all jobs and workers."""
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
    await backend.register_worker(
        worker_id="w1",
        hostname="host",
        pid=1234,
        queues=["default"],
        concurrency=4,
    )

    await backend.reset()

    assert await backend.get_job("j1") is None
    assert await backend.list_jobs() == []


async def test_get_queue_stats(backend):
    """get_queue_stats should return correct counts."""
    await backend.create_job(
        job_id="j1",
        job_name="mod.a",
        args={},
        args_hash=None,
        max_attempts=3,
        priority=100,
        queue="default",
        unique=False,
    )
    await backend.create_job(
        job_id="j2",
        job_name="mod.b",
        args={},
        args_hash=None,
        max_attempts=3,
        priority=100,
        queue="default",
        unique=False,
    )

    stats = await backend.get_queue_stats()
    assert set(stats.keys()) == {
        "total",
        "queued",
        "processing",
        "done",
        "dead_letter",
        "cancelled",
    }
    assert stats["queued"] == 2
    assert stats["total"] == 2
    assert stats["dead_letter"] == 0


async def test_expires_at_set_on_done(backend):
    """mark_job_done with TTL should set expires_at."""
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
    await backend.mark_job_done("j1", result_ttl=300)

    job = await backend.get_job("j1")
    assert job is not None
    assert job.get("expires_at") is not None
