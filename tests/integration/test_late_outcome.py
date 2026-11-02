"""
A worker that stalls past the heartbeat timeout loses its claim: the sweep
requeues the job and another worker claims it. When the stalled handler
finally returns, its outcome must not overwrite, requeue or dead-letter
the run the second worker now owns.
"""

import uuid

import pytest

from soniq import Soniq
from soniq.core.processor import process_job_via_backend
from tests.db_utils import TEST_DATABASE_URL


@pytest.fixture
async def app():
    a = Soniq(database_url=TEST_DATABASE_URL)
    await a.setup()
    yield a
    await a._backend.reset()
    async with a.backend.acquire() as conn:
        await conn.execute("DELETE FROM soniq_dead_letter_jobs")
    await a.close()


async def _register(app):
    worker_id = str(uuid.uuid4())
    await app.backend.register_worker(
        worker_id=worker_id,
        hostname="box",
        pid=1,
        queues=["default"],
        concurrency=1,
    )
    return worker_id


async def _steal(app, stalled, thief):
    """Age out the stalled worker, sweep, and claim its job as the thief."""
    async with app.backend.acquire() as conn:
        await conn.execute(
            "UPDATE soniq_workers SET last_heartbeat = NOW() - interval '1 hour'"
            " WHERE id = $1",
            uuid.UUID(stalled),
        )
    await app.backend.cleanup_stale_workers(stale_threshold_seconds=300)
    return await app.backend.fetch_and_lock_job(queues=["default"], worker_id=thief)


async def _dead_letter_count(app):
    async with app.backend.acquire() as conn:
        return await conn.fetchval("SELECT count(*) FROM soniq_dead_letter_jobs")


async def _assert_owned_by(app, job_id, worker_id, attempts):
    job = await app.backend.get_job(job_id)
    assert job["status"] == "processing"
    assert job["attempts"] == attempts
    assert job["last_error"] is None
    async with app.backend.acquire() as conn:
        owner = await conn.fetchval(
            "SELECT worker_id FROM soniq_jobs WHERE id = $1", uuid.UUID(job_id)
        )
    assert str(owner) == worker_id
    assert await _dead_letter_count(app) == 0


@pytest.mark.asyncio
async def test_late_backend_writes_miss_the_new_claim(app):
    a, b = await _register(app), await _register(app)
    job_id = str(uuid.uuid4())
    await app.backend.create_job(
        job_id=job_id,
        job_name="late.job",
        args={},
        args_hash=None,
        max_attempts=5,
        priority=100,
        queue="default",
        unique=False,
    )
    claim_a = await app.backend.fetch_and_lock_job(queues=["default"], worker_id=a)
    claim_b = await _steal(app, a, b)
    assert str(claim_b["id"]) == job_id
    late = claim_a["attempts"]

    backend = app.backend
    assert await backend.mark_job_done(job_id, claimed_attempts=late) is False
    assert (
        await backend.mark_job_done(job_id, result_ttl=0, claimed_attempts=late)
        is False
    )
    assert (
        await backend.mark_job_failed(
            job_id, attempts=late, error="x", retry_delay=5, claimed_attempts=late
        )
        is False
    )
    assert (
        await backend.reschedule_job(
            job_id, delay_seconds=60, attempts=0, claimed_attempts=late
        )
        is False
    )
    assert (
        await backend.mark_job_dead_letter(
            job_id, attempts=late, error="x", reason="r", claimed_attempts=late
        )
        is False
    )
    await _assert_owned_by(app, job_id, b, claim_b["attempts"])

    assert (
        await backend.mark_job_done(job_id, claimed_attempts=claim_b["attempts"])
        is True
    )
    assert (await backend.get_job(job_id))["status"] == "done"


@pytest.mark.parametrize("outcome", ["done", "failed", "dead_letter", "snooze"])
@pytest.mark.asyncio
async def test_processor_drops_a_late_outcome(app, outcome):
    from soniq import Snooze

    a, b = await _register(app), await _register(app)
    stolen = {}
    after_job = []
    app.after_job(lambda *args: after_job.append(args))

    @app.job(name="late.handler", retries=0 if outcome == "dead_letter" else 3)
    async def handler():
        # The handler stalled long enough for the sweep to hand its job on.
        stolen.update(await _steal(app, a, b))
        if outcome in ("failed", "dead_letter"):
            raise RuntimeError("late failure")
        if outcome == "snooze":
            return Snooze(seconds=60)
        return "late result"

    job_id = await app.enqueue(handler)
    await process_job_via_backend(
        backend=app.backend,
        job_registry=app._job_registry,
        worker_id=a,
        hooks=app._hooks,
        settings=app._settings,
    )

    await _assert_owned_by(app, job_id, b, stolen["attempts"])
    assert (await app.backend.get_job(job_id))["result"] is None
    assert after_job == []
