"""
Backend conformance: a late outcome from a reclaimed claim is dropped.

Worker A claims a job and stalls. The job goes back to queued (the
stale-worker sweep, or nack_job here, which writes the same row) and
worker B claims it. A's outcome writes, guarded by the attempt A
claimed, must leave B's run alone.
"""


async def _reclaimed_job(backend):
    await backend.create_job(
        job_id="j1",
        job_name="mod.func",
        args={},
        args_hash=None,
        max_attempts=5,
        priority=100,
        queue="default",
        unique=False,
    )
    a = await backend.fetch_and_lock_job(queues=["default"], worker_id=None)
    await backend.nack_job("j1")
    b = await backend.fetch_and_lock_job(queues=["default"], worker_id=None)
    assert (a["attempts"], b["attempts"]) == (1, 2)
    return a["attempts"], b["attempts"]


async def test_late_outcomes_do_not_touch_the_new_claim(backend):
    a, b = await _reclaimed_job(backend)

    assert await backend.mark_job_done("j1", claimed_attempts=a) is False
    assert await backend.mark_job_done("j1", result_ttl=0, claimed_attempts=a) is False
    assert (
        await backend.mark_job_failed("j1", attempts=a, error="x", claimed_attempts=a)
        is False
    )
    assert (
        await backend.reschedule_job(
            "j1", delay_seconds=60, attempts=0, claimed_attempts=a
        )
        is False
    )
    assert (
        await backend.mark_job_dead_letter(
            "j1", attempts=a, error="x", reason="r", claimed_attempts=a
        )
        is False
    )

    job = await backend.get_job("j1")
    assert job["status"] == "processing"
    assert job["attempts"] == b
    assert job["last_error"] is None

    # B's own outcome still lands.
    assert await backend.mark_job_done("j1", claimed_attempts=b) is True
    assert (await backend.get_job("j1"))["status"] == "done"


async def test_unguarded_outcome_still_writes(backend):
    await _reclaimed_job(backend)
    assert await backend.mark_job_failed("j1", attempts=2, error="x") is True
    assert (await backend.get_job("j1"))["status"] == "queued"
