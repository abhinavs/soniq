# Recipe: Scheduled Reports

A pattern for generating recurring reports on a cron schedule using `@app.periodic`.

## The job

```python
from soniq import Soniq

app = Soniq(database_url="postgresql://localhost/myapp")

@app.periodic(cron="0 9 * * 1")  # Every Monday at 9 AM
async def weekly_sales_report():
    start, end = get_last_week_range()
    data = await fetch_sales_data(start, end)
    report = generate_report(data)
    await send_report(
        to="team@example.com",
        subject=f"Sales report: {start:%b %d} - {end:%b %d}",
        attachment=report,
    )
```

The `@app.periodic` decorator registers both the job and its schedule. The scheduler picks it up automatically.

## Running the scheduler

The scheduler is a separate process that creates job instances on schedule. Run it alongside your worker:

```bash
# Terminal 1: Scheduler (creates jobs on schedule)
SONIQ_JOBS_MODULES="app.reports" soniq scheduler

# Terminal 2: Worker (processes created jobs)
SONIQ_JOBS_MODULES="app.reports" soniq worker
```

The scheduler checks registered periodic jobs and enqueues them when their schedule fires. The worker processes them like any other job.

## Schedule options

Cron expressions for specific times:

```python
@app.periodic(cron="0 9 * * *")       # Daily at 9 AM
async def daily_digest():
    ...

@app.periodic(cron="0 0 1 * *")       # First of every month
async def monthly_summary():
    ...

@app.periodic(cron="*/15 * * * *")     # Every 15 minutes
async def check_stale_orders():
    ...
```

Interval helpers for simpler cases (use the cron-string builders or a `timedelta`):

```python
from datetime import timedelta
from soniq import every

@app.periodic(cron=every(10).minutes())
async def cleanup_temp_files():
    ...

@app.periodic(cron=every(1).hours())
async def sync_inventory():
    ...

# Sub-minute uses every= directly (cron has no second resolution).
@app.periodic(every=timedelta(seconds=30))
async def health_ping():
    ...
```

You can combine `@periodic` with any `@job` option:

```python
@app.periodic(cron="0 9 * * 1", queue="reports", max_retries=2, timeout=300)
async def weekly_report():
    ...
```

## Complete example

```python
# app/reports.py
from soniq import Soniq

app = Soniq(database_url="postgresql://localhost/myapp")


@app.periodic(cron="0 9 * * 1", queue="reports", timeout=300)
async def weekly_sales_report():
    start, end = get_last_week_range()
    data = await fetch_sales_data(start, end)
    report = generate_csv(data)
    await upload_to_s3(report, key=f"reports/sales-{start:%Y%m%d}.csv")
    await send_email(
        to="team@example.com",
        subject=f"Weekly sales: {start:%b %d} - {end:%b %d}",
        attachment_url=report.url,
    )


@app.periodic(cron="0 6 * * *", queue="reports")
async def daily_error_digest():
    errors = await fetch_errors_since_yesterday()
    if not errors:
        return  # Nothing to report
    await send_email(
        to="oncall@example.com",
        subject=f"Error digest: {len(errors)} errors",
        body=format_error_summary(errors),
    )
```

```bash
# Run everything
export SONIQ_DATABASE_URL="postgresql://localhost/myapp"
export SONIQ_JOBS_MODULES="app.reports"

# In separate terminals:
soniq scheduler
soniq worker --queues reports --concurrency 2
```
