"""
Backend conformance: unique job deduplication and dedup_key.
"""


async def test_unique_job_returns_existing_id(backend):
    """Enqueueing a duplicate unique job returns the existing ID."""
    id1 = await backend.create_job(
        job_id="a",
        job_name="mod.func",
        args={"x": 1},
        args_hash="h1",
        max_attempts=3,
        priority=100,
        queue="default",
        unique=True,
    )
    id2 = await backend.create_job(
        job_id="b",
        job_name="mod.func",
        args={"x": 1},
        args_hash="h1",
        max_attempts=3,
        priority=100,
        queue="default",
        unique=True,
    )
    assert id1 == id2


async def test_dedup_key_dedup(backend):
    """Jobs with the same dedup_key are deduplicated."""
    id1 = await backend.create_job(
        job_id="a",
        job_name="mod.func",
        args={"x": 1},
        args_hash=None,
        max_attempts=3,
        priority=100,
        queue="default",
        unique=False,
        dedup_key="lock1",
    )
    id2 = await backend.create_job(
        job_id="b",
        job_name="mod.func",
        args={"x": 2},
        args_hash=None,
        max_attempts=3,
        priority=100,
        queue="default",
        unique=False,
        dedup_key="lock1",
    )
    assert id1 == id2


async def test_unique_allows_requeue_after_done(backend):
    """Unique dedup only applies to 'queued' status."""
    await backend.create_job(
        job_id="a",
        job_name="mod.func",
        args={"x": 1},
        args_hash="h1",
        max_attempts=3,
        priority=100,
        queue="default",
        unique=True,
    )
    await backend.fetch_and_lock_job(queues=["default"], worker_id=None)
    await backend.mark_job_done("a", result_ttl=0)

    id2 = await backend.create_job(
        job_id="b",
        job_name="mod.func",
        args={"x": 1},
        args_hash="h1",
        max_attempts=3,
        priority=100,
        queue="default",
        unique=True,
    )
    assert id2 == "b"
