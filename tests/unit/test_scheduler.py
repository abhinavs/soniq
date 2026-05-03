"""Unit tests for the Scheduler service against MemoryBackend.

Covers the public CRUD surface (add / pause / resume / remove / list / get)
plus the decorator-driven registration path used by `Scheduler.start()` to
materialize @app.periodic functions on startup.

The scheduler-loop tick (advisory-lock leader election + atomic claim +
enqueue + bookkeeping) is exercised against a real Postgres in
`tests/integration/test_scheduler_leader.py`. Memory mode runs single-
writer so these unit tests don't try to reproduce the race.
"""

from datetime import timedelta

import pytest

from soniq import Soniq, daily, every


@pytest.fixture
async def app():
    a = Soniq(backend="memory")
    await a._ensure_initialized()
    try:
        yield a
    finally:
        await a.close()


@pytest.mark.asyncio
async def test_add_returns_name_and_persists(app):
    @app.job(name="reports.daily")
    async def daily_report():
        return None

    name = await app.scheduler.add(daily_report, cron=daily().at("09:00"))
    assert name == "reports.daily"

    sched = await app.scheduler.get("reports.daily")
    assert sched is not None
    assert sched["schedule_type"] == "cron"
    assert sched["schedule_value"] == "0 9 * * *"
    assert sched["status"] == "active"
    assert sched["next_run"] is not None


@pytest.mark.asyncio
async def test_add_with_every_timedelta(app):
    @app.job(name="metrics.flush")
    async def flush():
        return None

    await app.scheduler.add(flush, every=timedelta(seconds=30))
    sched = await app.scheduler.get("metrics.flush")
    assert sched["schedule_type"] == "interval"
    assert sched["schedule_value"] == "30"


@pytest.mark.asyncio
async def test_add_idempotent_on_same_name(app):
    @app.job(name="cache.warm")
    async def warm():
        return None

    await app.scheduler.add(warm, cron=every(5).minutes())
    await app.scheduler.add(warm, cron=every(10).minutes())

    schedules = await app.scheduler.list()
    assert len(schedules) == 1
    assert schedules[0]["schedule_value"] == "*/10 * * * *"


@pytest.mark.asyncio
async def test_pause_resume_remove(app):
    @app.job(name="t1")
    async def t1():
        return None

    await app.scheduler.add(t1, cron=daily().at("06:00"))

    assert await app.scheduler.pause("t1") is True
    sched = await app.scheduler.get("t1")
    assert sched["status"] == "paused"

    assert await app.scheduler.resume("t1") is True
    sched = await app.scheduler.get("t1")
    assert sched["status"] == "active"

    assert await app.scheduler.remove("t1") is True
    assert await app.scheduler.get("t1") is None
    # Removing a missing schedule reports False rather than raising.
    assert await app.scheduler.remove("t1") is False


@pytest.mark.asyncio
async def test_list_filter_by_status(app):
    @app.job(name="a")
    async def a():
        return None

    @app.job(name="b")
    async def b():
        return None

    await app.scheduler.add(a, cron=daily().at("01:00"))
    await app.scheduler.add(b, cron=daily().at("02:00"))
    await app.scheduler.pause("b")

    active = await app.scheduler.list(status="active")
    paused = await app.scheduler.list(status="paused")
    assert {s["name"] for s in active} == {"a"}
    assert {s["name"] for s in paused} == {"b"}


@pytest.mark.asyncio
async def test_periodic_decorator_registers_via_start(app):
    @app.periodic(cron=daily().at("12:00"), name="lunch")
    async def lunch():
        return None

    # Before start(), nothing has been materialized into the scheduler's
    # store - the decorator only stamps metadata.
    assert await app.scheduler.list() == []

    await app.scheduler.start(check_interval=3600)
    try:
        sched = await app.scheduler.get("lunch")
        assert sched is not None
        assert sched["schedule_value"] == "0 12 * * *"
    finally:
        await app.scheduler.stop()


@pytest.mark.asyncio
async def test_periodic_with_every_timedelta_decorator(app):
    @app.periodic(every=timedelta(seconds=15), name="ping")
    async def ping():
        return None

    await app.scheduler.start(check_interval=3600)
    try:
        sched = await app.scheduler.get("ping")
        assert sched is not None
        assert sched["schedule_type"] == "interval"
        assert sched["schedule_value"] == "15"
    finally:
        await app.scheduler.stop()


@pytest.mark.asyncio
async def test_add_rejects_both_cron_and_every(app):
    @app.job(name="x")
    async def x():
        return None

    with pytest.raises(ValueError):
        await app.scheduler.add(x, cron="* * * * *", every=timedelta(seconds=10))


@pytest.mark.asyncio
async def test_add_rejects_neither_cron_nor_every(app):
    @app.job(name="y")
    async def y():
        return None

    with pytest.raises(ValueError):
        await app.scheduler.add(y)


@pytest.mark.asyncio
async def test_add_rejects_invalid_cron(app):
    @app.job(name="z")
    async def z():
        return None

    with pytest.raises(ValueError):
        await app.scheduler.add(z, cron="not a cron")


@pytest.mark.asyncio
async def test_periodic_decorator_rejects_both(app):
    with pytest.raises(ValueError):

        @app.periodic(cron="* * * * *", every=timedelta(seconds=10), name="bad")
        async def bad():
            return None


@pytest.mark.asyncio
async def test_periodic_decorator_rejects_neither(app):
    with pytest.raises(ValueError):

        @app.periodic(name="bad2")
        async def bad():
            return None
