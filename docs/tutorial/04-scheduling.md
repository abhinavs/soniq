# 4. Scheduling

> **Intermediate** - 8 minutes. Delayed jobs and recurring schedules.

Soniq supports one-off delayed jobs and recurring schedules. The
recurring scheduler ships in the default install (`croniter` is a
default dependency as of 0.0.2) - no extra needed.

## One-off scheduling

Schedule a job to run at a specific time or after a delay.

**Absolute time:**

```python
from datetime import datetime, timezone

await app.schedule(
    send_welcome_email,
    run_at=datetime(2025, 1, 15, 9, 0, tzinfo=timezone.utc),
    user_id=42,
)
```

**Relative delay:**

```python
from datetime import timedelta

await app.schedule(send_report, run_at=timedelta(minutes=30), report_id="q4")
await app.schedule(send_report, run_at=600, report_id="q4")  # 600 seconds from now
```

`run_at` accepts a UTC `datetime`, a `timedelta`, or a number of seconds from now. Under the hood, `schedule()` calls `enqueue()` with a `scheduled_at` timestamp. The worker ignores scheduled jobs until their time arrives.

## Recurring jobs

### `@app.periodic()` decorator

The single decorator that registers a job and its schedule together. The scheduler process picks up all `@periodic` functions automatically.

```python
from datetime import timedelta
from soniq import cron, daily, every, monthly, weekly

# Cron expression (string or builder).
@app.periodic(cron=daily().at("09:00"), name="reports.daily")
async def daily_sales_report():
    ...

# Plain cron strings work too.
@app.periodic(cron="0 9 * * 1-5", name="weekday.morning")
async def weekday_summary():
    ...

# Interval (cron has no sub-minute resolution; pass a timedelta).
@app.periodic(every=timedelta(seconds=30), name="metrics.flush")
async def flush_metrics():
    ...
```

`@app.periodic` accepts the same kwargs as `@app.job` (`name`, `queue`, `priority`, `retries`, `validate`, etc.) - it is `@app.job` plus the periodic stamp. `name` is optional and falls back to `f"{module}.{qualname}"`.

### Cron-string builders

`soniq.schedules` exposes a small DSL that returns plain cron strings - a readability layer over the 5-field grammar. Each terminal returns a `str`, so `cron=daily().at("09:00")` works without `.expr`.

One big example, every builder in one place:

```python
from datetime import timedelta
from soniq import Soniq, cron, daily, every, monthly, weekly

app = Soniq(database_url="postgresql://localhost/myapp")


# every(N).minutes() -> "*/N * * * *"
# Fires at :00, :05, :10, ..., :55 of every hour.
@app.periodic(cron=every(5).minutes(), name="metrics.scrape")
async def scrape_metrics():
    await collector.scrape()


# every(N).hours() -> "0 */N * * *"
# Fires at 00:00, 02:00, 04:00, ..., 22:00 UTC. Always on the hour.
@app.periodic(cron=every(2).hours(), name="cache.refresh")
async def refresh_cache():
    await cache.refresh_all()


# every(N).seconds() -> timedelta(seconds=N), NOT a cron string.
# Cron has no sub-minute resolution, so pass via `every=`, not `cron=`.
# Equivalent to `every=timedelta(seconds=30)` - the builder is just a readable alias.
@app.periodic(every=every(30).seconds(), name="health.ping")
async def ping_upstream():
    await healthcheck.ping()


# daily().at("HH:MM") -> "M H * * *"
# Fires once a day at the given UTC time. 24-hour clock.
@app.periodic(cron=daily().at("09:00"), name="reports.daily")
async def send_daily_report():
    await report_service.send_daily()


# weekly().on("DAY").at("HH:MM") -> "M H * * D"
# Fires once a week. on() accepts a day name ("monday".."sunday", case-insensitive)
# or an integer (0=Sunday .. 6=Saturday).
@app.periodic(cron=weekly().on("monday").at("09:00"), name="reports.weekly")
async def send_weekly_summary():
    await report_service.send_weekly()


# monthly().on_day(N).at("HH:MM") -> "M H N * *"
# Fires once a month on day-of-month N (1-31). If the month does not have that
# day (e.g. day 31 in February), cron simply skips that month.
@app.periodic(cron=monthly().on_day(15).at("12:00"), name="billing.midmonth")
async def run_midmonth_billing():
    await billing.run_midmonth()


# cron("EXPR") -> identity passthrough; returns the string unchanged.
# Escape hatch for the full 5-field grammar (ranges, lists, step+offset combos)
# or expressions copied from elsewhere. `cron="0 9 * * 1-5"` is equivalent.
@app.periodic(cron=cron("0 9 * * 1-5"), name="weekday.morning")
async def weekday_morning_digest():
    await digest.send()
```

### Imperative API for dynamic schedules

When a schedule is computed at runtime (per-tenant, per-feature-flag, ...), use `app.scheduler.add(...)`:

```python
await app.scheduler.add(
    target=cleanup,                # callable, registered task name, or use name=
    cron="0 9 * * *",
    args={"region": "US"},
    queue="reports",
    priority=10,
)

await app.scheduler.pause("reports.daily")
await app.scheduler.resume("reports.daily")
await app.scheduler.remove("reports.daily")

schedules = await app.scheduler.list(status="active")
sched = await app.scheduler.get("reports.daily")
```

Schedules are keyed by the resolved task name. Calling `add()` again with the same name updates the schedule in place rather than creating a duplicate.

## Scheduler process

Recurring jobs need a running scheduler to check for due jobs and enqueue them. Start it separately from your worker:

```bash
soniq scheduler --check-interval 60
```

The scheduler checks for due recurring jobs every `--check-interval` seconds (default: 60). When a job is due, it enqueues a new instance into the regular job queue. Workers then pick it up as usual.

Multiple scheduler instances are safe. Soniq elects a single tick leader via a Postgres advisory lock per interval, and the per-job claim runs as an atomic compare-and-swap on `next_run` inside a single transaction with the enqueue: if anyone wins the race, exactly one job lands in the queue.

### Inspecting registered schedules

```bash
soniq inspect              # worker list + schedule counts
soniq inspect --schedules  # plus each schedule by name with next-run time
```

Scheduler liveness is leader-elected per tick rather than tracked as a
persistent process record, so `inspect` reports what is registered, not which
process is currently the leader. To check a scheduler process is alive, look
at your process supervisor.

### Programmatic start

```python
await app.scheduler.start(check_interval=30)
# ...later...
await app.scheduler.stop()
```

## Persistence

Recurring job schedules are stored in the `soniq_recurring_jobs` table on Postgres. If the scheduler restarts, it reloads all active schedules and resumes where it left off. No schedules are lost. The Memory and SQLite backends keep schedules in-process (single-writer, useful for tests and local dev).
