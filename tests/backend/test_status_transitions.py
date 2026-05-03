"""
Backend conformance: job status transitions.
"""


async def test_full_lifecycle_success(backend):
    """queued -> processing -> done"""
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
    assert record is not None

    job = await backend.get_job("j1")
    assert job["status"] == "processing"

    await backend.mark_job_done("j1", result_ttl=300)
    job = await backend.get_job("j1")
    assert job["status"] == "done"


async def test_failure_and_retry(backend):
    """queued -> processing -> failed (requeued) -> processing -> dead_letter (moved out)"""
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

    # First attempt
    await backend.fetch_and_lock_job(queues=["default"], worker_id=None)
    await backend.mark_job_failed("j1", attempts=1, error="boom", retry_delay=None)

    job = await backend.get_job("j1")
    assert job["status"] == "queued"

    # Second attempt -> dead letter (row leaves soniq_jobs entirely)
    await backend.fetch_and_lock_job(queues=["default"], worker_id=None)
    await backend.mark_job_dead_letter(
        "j1",
        attempts=2,
        error="boom again",
        reason="max_retries_exceeded",
    )

    assert await backend.get_job("j1") is None


async def test_cancel_only_works_on_queued(backend):
    """Cannot cancel a processing or done job."""
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

    # Cancel while queued should work
    assert await backend.cancel_job("j1") is True

    job = await backend.get_job("j1")
    assert job["status"] == "cancelled"

    # Cancel again should fail (already cancelled)
    assert await backend.cancel_job("j1") is False


async def test_result_ttl_zero_deletes_immediately(backend):
    """mark_job_done with result_ttl=0 should delete the job."""
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
    await backend.mark_job_done("j1", result_ttl=0)

    assert await backend.get_job("j1") is None


async def test_dead_letter_removes_row_from_soniq_jobs(backend):
    """Under DLQ Option A the row leaves ``soniq_jobs`` on dead-letter
    and lives exclusively in ``soniq_dead_letter_jobs``. Resurrection
    lives on ``DeadLetterService.replay`` - see
    ``docs/_internals/contracts/dead_letter.md``."""
    await backend.create_job(
        job_id="j1",
        job_name="mod.func",
        args={},
        args_hash=None,
        max_attempts=1,
        priority=100,
        queue="default",
        unique=False,
    )
    await backend.fetch_and_lock_job(queues=["default"], worker_id=None)
    await backend.mark_job_dead_letter(
        "j1",
        attempts=1,
        error="permanent",
        reason="max_retries_exceeded",
    )

    assert await backend.get_job("j1") is None
