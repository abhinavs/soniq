---
description: Schedule Python background jobs in Soniq. Run a job later with soniq.schedule, or on a cron or interval with @soniq.periodic and the soniq scheduler process.
---

# 4. Schedule delayed and recurring jobs

> **Intermediate** - 8 minutes. Delayed jobs and recurring schedules.

Soniq can run a job once at a later time, or repeatedly on a cron or interval schedule. The recurring scheduler is part of the default install (`croniter` is a core dependency), so you need no extra.

## One-off scheduling

Run a job at a set time or after a delay.

**Absolute time:**

```python
from datetime import datetime, timezone

await soniq.schedule(
    send_welcome_email,
    run_at=datetime(2030, 1, 15, 9, 0, tzinfo=timezone.utc),
    user_id=42,
)
```

**Relative delay:**

```python
from datetime import timedelta

await soniq.schedule(send_report, run_at=timedelta(minutes=30), report_id="q4")
await soniq.schedule(send_report, run_at=600, report_id="q4")  # 600 seconds from now
```

`run_at` accepts a timezone-aware `datetime`, a `timedelta`, or a number of seconds from now. Soniq rejects a naive `datetime` (one without `tzinfo`). `schedule()` calls `enqueue()` with a `scheduled_at` value. The worker does not claim a scheduled job before its time.

## Recurring jobs

### `@soniq.periodic()` decorator

This decorator registers a job and its schedule together. The scheduler process finds every `@soniq.periodic()` function automatically.

```python
from datetime import timedelta
from soniq import cron, daily, every, monthly, weekly

# Cron expression (string or builder).
@soniq.periodic(cron=daily().at("09:00"), name="reports.daily")
async def daily_sales_report():
    ...

# Plain cron strings work too.
@soniq.periodic(cron="0 9 * * 1-5", name="weekday.morning")
async def weekday_summary():
    ...

# Interval (cron has no sub-minute resolution; pass a timedelta).
@soniq.periodic(every=timedelta(seconds=30), name="metrics.flush")
async def flush_metrics():
    ...
```

`@soniq.periodic()` accepts the same keyword arguments as `@soniq.job()` (`name`, `queue`, `priority`, `retries`, `validate`, and so on). Pass exactly one of `cron=` or `every=`. Always set `name=`. Without it, Soniq derives a name from the module path and prints a deprecation warning.

### Cron-string builders

The `soniq.schedules` module has small builders that return plain cron strings. They make the 5-field cron grammar easier to read. Each builder ends in a method that returns a `str`, so `cron=daily().at("09:00")` works as it is.

This example shows every builder:

```python
from datetime import timedelta
from soniq import Soniq, cron, daily, every, monthly, weekly

soniq = Soniq(database_url="postgresql://localhost/myapp")


# every(N).minutes() -> "*/N * * * *"
# Fires at :00, :05, :10, ..., :55 of every hour.
@soniq.periodic(cron=every(5).minutes(), name="metrics.scrape")
async def scrape_metrics():
    await collector.scrape()


# every(N).hours() -> "0 */N * * *"
# Fires at 00:00, 02:00, 04:00, ..., 22:00 UTC. Always on the hour.
@soniq.periodic(cron=every(2).hours(), name="cache.refresh")
async def refresh_cache():
    await cache.refresh_all()


# every(N).seconds() -> timedelta(seconds=N), NOT a cron string.
# Cron has no sub-minute resolution, so pass via `every=`, not `cron=`.
# Equivalent to `every=timedelta(seconds=30)` - the builder is just a readable alias.
@soniq.periodic(every=every(30).seconds(), name="health.ping")
async def ping_upstream():
    await healthcheck.ping()


# daily().at("HH:MM") -> "M H * * *"
# Fires once a day at the given UTC time. 24-hour clock.
@soniq.periodic(cron=daily().at("09:00"), name="reports.daily")
async def send_daily_report():
    await report_service.send_daily()


# weekly().on("DAY").at("HH:MM") -> "M H * * D"
# Fires once a week. on() accepts a day name ("monday".."sunday", case-insensitive)
# or an integer (0=Sunday .. 6=Saturday).
@soniq.periodic(cron=weekly().on("monday").at("09:00"), name="reports.weekly")
async def send_weekly_summary():
    await report_service.send_weekly()


# monthly().on_day(N).at("HH:MM") -> "M H N * *"
# Fires once a month on day-of-month N (1-31). If the month does not have that
# day (e.g. day 31 in February), cron simply skips that month.
@soniq.periodic(cron=monthly().on_day(15).at("12:00"), name="billing.midmonth")
async def run_midmonth_billing():
    await billing.run_midmonth()


# cron("EXPR") -> identity passthrough; returns the string unchanged.
# Escape hatch for the full 5-field grammar (ranges, lists, step+offset combos)
# or expressions copied from elsewhere. `cron="0 9 * * 1-5"` is equivalent.
@soniq.periodic(cron=cron("0 9 * * 1-5"), name="weekday.morning")
async def weekday_morning_digest():
    await digest.send()
```

### Imperative API for dynamic schedules

When your code computes a schedule at runtime (for example per tenant), use `soniq.scheduler.add(...)`:

```python
await soniq.scheduler.add(
    target=cleanup,                # callable, registered task name, or use name=
    cron="0 9 * * *",
    args={"region": "US"},
    queue="reports",
    priority=10,
)

await soniq.scheduler.pause("reports.daily")
await soniq.scheduler.resume("reports.daily")
await soniq.scheduler.remove("reports.daily")

schedules = await soniq.scheduler.list(status="active")
sched = await soniq.scheduler.get("reports.daily")
```

Soniq identifies a schedule by its task name. If you call `add()` again with the same name, Soniq updates the schedule and does not create a duplicate.

## Scheduler process

Recurring jobs need a running scheduler. The scheduler finds due jobs and enqueues them. Start it in a separate process from your worker:

```bash
soniq scheduler --check-interval 60
```

The scheduler checks for due jobs every `--check-interval` seconds (default: 60). If an `every=` schedule is shorter than that, the scheduler uses the shorter interval. For each due job, the scheduler enqueues a new job in the regular queue. Workers claim it as usual.

You can run more than one scheduler. The schedulers elect one leader for each tick with a PostgreSQL advisory lock. The claim for each job is an atomic compare-and-swap on `next_run`, in the same transaction as the enqueue. Only one scheduler wins, so exactly one job enters the queue.

### Inspecting registered schedules

```bash
soniq inspect              # worker list + schedule counts
soniq inspect --schedules  # plus each schedule by name with next-run time
```

Soniq elects the scheduler leader for each tick. It does not keep a process record for the scheduler. `soniq inspect` therefore shows what is registered, not which process is the leader. To check that a scheduler process is alive, use your process supervisor.

### Programmatic start

```python
await soniq.scheduler.start(check_interval=30)
# ...later...
await soniq.scheduler.stop()
```

## Persistence

On PostgreSQL, Soniq stores recurring schedules in the `soniq_recurring_jobs` table. When the scheduler restarts, it reloads all active schedules and continues. The memory and SQLite backends keep schedules in the process. Use them for tests and local development.

## Next steps

Continue with [chapter 5: queues](05-queues.md).
