"""Multi-scheduler race tests against real Postgres.

The Scheduler service uses two layered guards to keep duplicate enqueues
from happening when several scheduler processes share a database:

1. An advisory-lock leader guard that lets one scheduler per tick scan
   for due jobs.
2. An optimistic compare-and-swap on `next_run` inside the
   claim+enqueue+bookkeeping transaction so that *if* two schedulers
   somehow both reached step 3, exactly one would land its UPDATE.

These tests exercise (2) directly, which is the correctness floor. (1)
is an efficiency optimization on top: the integration is effectively
covered by `tests/integration/test_leader_election.py`.
"""

from datetime import datetime, timedelta, timezone

import pytest

from soniq import Soniq
from soniq.features.scheduler import _calculate_next_run
from tests.db_utils import TEST_DATABASE_URL


@pytest.fixture
async def app():
    a = Soniq(database_url=TEST_DATABASE_URL)
    await a._ensure_initialized()
    pool = await a._get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM soniq_recurring_jobs")
    try:
        yield a
    finally:
        await a.close()


@pytest.mark.asyncio
async def test_concurrent_claim_only_one_advances(app):
    """Two scheduler processes evaluating the same due job at the same
    instant must not both advance the schedule.

    Setup: insert a recurring schedule whose next_run is in the past. Run
    two `_execute_due` calls concurrently against the same schedule
    snapshot. Exactly one must observe rows_affected == 1 from the
    optimistic claim; the other must read back the new next_run and bail
    without enqueuing.
    """

    @app.job(name="periodic.race")
    async def race():
        return None

    past = datetime.now(timezone.utc) - timedelta(seconds=30)
    name = await app.scheduler.add(race, every=timedelta(seconds=60))
    # Force next_run into the past via a direct UPDATE so the schedulers
    # both think it is due right now. Then invalidate the cache so the
    # subsequent get() calls reflect the DB-truth `past` and not the
    # cached "now+60s" value that add() seeded.
    pool = await app._get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE soniq_recurring_jobs SET next_run = $1 WHERE job_name = $2",
            past,
            name,
        )
    app.scheduler._loaded = False
    app.scheduler._cache.clear()

    # Each scheduler builds its own in-memory snapshot of the schedule
    # (mimicking two processes that loaded the row at the same time).
    sched_a = await app.scheduler.get(name)
    sched_b = await app.scheduler.get(name)
    # `Scheduler.get` returns dicts; rebuild the internal record so we
    # can call the private execute path.
    from soniq.features.scheduler import _Schedule

    def _to_record(d) -> _Schedule:
        return _Schedule(
            id=d["id"],
            name=d["name"],
            schedule_type=d["schedule_type"],
            schedule_value=d["schedule_value"],
            priority=d["priority"],
            queue=d["queue"],
            max_attempts=d["max_attempts"],
            args=d["args"],
            status=d["status"],
            created_at=d["created_at"],
            last_run=d["last_run"],
            next_run=d["next_run"],
            run_count=d["run_count"],
            last_job_id=d["last_job_id"],
        )

    rec_a = _to_record(sched_a)
    rec_b = _to_record(sched_b)
    now = datetime.now(timezone.utc)

    import asyncio

    await asyncio.gather(
        app.scheduler._execute_due(rec_a, now),
        app.scheduler._execute_due(rec_b, now),
    )

    # Exactly one job row should land in the queue: the loser CAS rolls
    # back to a no-op without producing a job.
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id FROM soniq_jobs WHERE job_name = $1", "periodic.race"
        )
    assert (
        len(rows) == 1
    ), f"Expected exactly one enqueued job from concurrent claim race; got {len(rows)}"

    # And the schedule must have advanced past `past` to the next interval.
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT next_run, run_count FROM soniq_recurring_jobs WHERE job_name = $1",
            "periodic.race",
        )
    assert row["next_run"] > past
    assert row["run_count"] == 1


@pytest.mark.asyncio
async def test_advisory_lock_serializes_ticks(app):
    """Only one Scheduler at a time holds the `soniq.scheduler` advisory
    lock, so concurrent ticks from a fleet do not all redundantly scan
    for due jobs. We don't try to fingerprint internal scan counts -
    instead we assert the public guarantee: under contention, at most one
    instance is a leader at a time.
    """
    from soniq.core.leadership import with_advisory_lock

    backend = app.backend

    async with with_advisory_lock(backend, "soniq.scheduler") as leader_a:
        assert leader_a is True
        async with with_advisory_lock(backend, "soniq.scheduler") as leader_b:
            # Same backend, same lock name, contended in a different
            # session -> the second acquire must fail.
            assert leader_b is False


@pytest.mark.asyncio
async def test_calculate_next_run_advances_for_interval():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    nxt = _calculate_next_run("interval", "60", now)
    assert nxt == now + timedelta(seconds=60)


@pytest.mark.asyncio
async def test_calculate_next_run_advances_for_cron():
    now = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
    nxt = _calculate_next_run("cron", "0 9 * * *", now)
    assert nxt == datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)
