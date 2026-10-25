"""
A worker is dead only when its heartbeat is stale.

Status alone can't tell: a stalled worker can recover after the sweep
marks it stopped, a 0.1.0 worker never sets itself back to active, and
two live workers can share hostname and pid (host-network containers,
both pid 1).
"""

import uuid

import pytest

from soniq import Soniq
from tests.db_utils import TEST_DATABASE_URL


@pytest.fixture
async def app():
    a = Soniq(database_url=TEST_DATABASE_URL)
    await a.setup()
    yield a
    await a._backend.reset()
    await a.close()


async def _register(app, hostname="box", pid=1):
    worker_id = str(uuid.uuid4())
    await app.backend.register_worker(
        worker_id=worker_id,
        hostname=hostname,
        pid=pid,
        queues=["default"],
        concurrency=1,
    )
    return worker_id


async def _claimed_job(app, worker_id):
    job_id = str(uuid.uuid4())
    await app.backend.create_job(
        job_id=job_id,
        job_name="liveness.job",
        args={},
        args_hash=None,
        max_attempts=3,
        priority=100,
        queue="default",
        unique=False,
    )
    claimed = await app.backend.fetch_and_lock_job(
        queues=["default"], worker_id=worker_id
    )
    assert str(claimed["id"]) == job_id
    return job_id


async def _sql(app, query, *args):
    async with app.backend.acquire() as conn:
        return await conn.fetchval(query, *args)


async def _job_status(app, job_id):
    return (await app.backend.get_job(job_id))["status"]


@pytest.mark.asyncio
async def test_heartbeat_restores_a_worker_the_sweep_marked_stopped(app):
    worker_id = await _register(app)
    # The event loop stalled past the timeout and the sweep caught it.
    await _sql(
        app,
        "UPDATE soniq_workers SET last_heartbeat = NOW() - interval '1 hour'"
        " WHERE id = $1",
        uuid.UUID(worker_id),
    )
    assert await app.backend.cleanup_stale_workers(stale_threshold_seconds=300) == 1

    await app.backend.update_heartbeat(worker_id)
    status = await _sql(
        app, "SELECT status FROM soniq_workers WHERE id = $1", uuid.UUID(worker_id)
    )
    assert status == "active"

    job_id = await _claimed_job(app, worker_id)
    await app.backend.cleanup_stale_workers(stale_threshold_seconds=300)
    assert await _job_status(app, job_id) == "processing"


@pytest.mark.asyncio
async def test_sweep_leaves_a_stopped_but_heartbeating_worker_alone(app):
    # A 0.1.0 worker heartbeats without touching status, so it stays
    # 'stopped' after a sweep even though it is alive and claiming jobs.
    worker_id = await _register(app)
    job_id = await _claimed_job(app, worker_id)
    await _sql(
        app,
        "UPDATE soniq_workers SET status = 'stopped', last_heartbeat = NOW()"
        " WHERE id = $1",
        uuid.UUID(worker_id),
    )

    await app.backend.cleanup_stale_workers(stale_threshold_seconds=300)
    assert await _job_status(app, job_id) == "processing"


@pytest.mark.asyncio
async def test_same_host_and_pid_does_not_take_over_a_live_worker(app):
    live = await _register(app)
    job_id = await _claimed_job(app, live)

    impostor = await _register(app)

    assert await _job_status(app, job_id) == "processing"
    hostname = await _sql(
        app, "SELECT hostname FROM soniq_workers WHERE id = $1", uuid.UUID(impostor)
    )
    assert hostname == f"box#{impostor[:8]}"
    # The live worker keeps its row, so it can still claim and finish work.
    await app.backend.mark_job_done(job_id)
    await _claimed_job(app, live)


@pytest.mark.asyncio
async def test_heartbeat_registers_again_when_the_row_is_gone(app):
    worker_id = await _register(app)
    await _sql(app, "DELETE FROM soniq_workers WHERE id = $1", uuid.UUID(worker_id))

    await app.backend.update_heartbeat(worker_id)

    # Without the row, the claim would fail on soniq_jobs_worker_id_fkey.
    await _claimed_job(app, worker_id)


@pytest.mark.asyncio
async def test_empty_dedup_key_dedups_instead_of_raising(app):
    @app.job(name="liveness.dedup")
    async def dedup_job():
        pass

    first = await app.enqueue(dedup_job, dedup_key="")
    second = await app.enqueue(dedup_job, dedup_key="")
    assert first == second
