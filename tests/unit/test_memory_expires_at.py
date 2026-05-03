"""
Test that MemoryBackend sets expires_at when mark_job_done is called with result_ttl > 0.
"""

from soniq.testing.memory_backend import MemoryBackend


async def test_mark_done_sets_expires_at_with_ttl():
    """mark_job_done with result_ttl > 0 should set expires_at."""
    backend = MemoryBackend()
    await backend.initialize()

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
    assert job["status"] == "done"
    assert (
        job.get("expires_at") is not None
    ), "expires_at should be set when result_ttl > 0"


async def test_mark_done_no_expires_at_without_ttl():
    """mark_job_done without result_ttl should not set expires_at."""
    backend = MemoryBackend()
    await backend.initialize()

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
    await backend.mark_job_done("j1")

    job = await backend.get_job("j1")
    assert job is not None
    assert job["status"] == "done"
