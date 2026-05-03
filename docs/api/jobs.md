# Jobs

Everything about defining, enqueueing, scheduling, and inspecting jobs.


## @app.job decorator

Registers a function as a job. Always called with parentheses, even with no kwargs.

```python
app = Soniq(database_url="postgresql://localhost/myapp")

@app.job()
async def send_email(to: str, subject: str, body: str):
    ...

@app.job(retries=5, queue="urgent")
async def send_password_reset(to: str, token: str):
    ...
```

### Parameters

All parameters are optional.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `name` | `str \| None` | `None` | Explicit task name. When omitted, derived from `f"{module}.{qualname}"` (Celery-style). Pass an explicit value for cross-service deployments where the name is a wire-protocol identifier. |
| `retries` | `int` | `3` | Maximum retry attempts on failure. Alias: `max_retries`. |
| `priority` | `int` | `100` | Lower number = higher priority. Range: 1--1000. |
| `queue` | `str` | `"default"` | Queue name for this job. |
| `unique` | `bool` | `False` | Deduplicate by arguments hash. If a matching job is already queued, the enqueue is skipped. |
| `retry_delay` | `int \| float \| list[int \| float]` | `0` | Seconds to wait before each retry. Pass a list to set per-attempt delays (e.g. `[1, 5, 30]`). |
| `retry_backoff` | `bool` | `False` | Apply exponential backoff to `retry_delay`. |
| `retry_max_delay` | `int \| float \| None` | `None` | Cap on retry delay in seconds. |
| `timeout` | `int \| float \| None` | `None` | Per-job timeout in seconds. `None` uses the global `job_timeout` setting (default 300s). |
| `validate` | `type[BaseModel] \| None` | `None` | Pydantic model for argument validation at enqueue time. Alias: `args_model`. |

```python
@app.job(
    retries=5,
    priority=10,
    queue="urgent",
    retry_delay=[1, 5, 30, 60],
    timeout=120,
)
async def process_payment(order_id: int, amount: float):
    ...
```


## enqueue()

Dispatches a registered job for processing. Three input shapes:

```python
# 1. Callable (single-repo)
job_id = await app.enqueue(send_email, to="a@b.com", subject="Hi", body="Hello")

# 2. String task name (cross-service / by-name)
job_id = await app.enqueue(
    "users.send_email",
    args={"to": "a@b.com", "subject": "Hi", "body": "Hello"},
)

# 3. TaskRef (typed cross-repo stub)
job_id = await app.enqueue(send_email_ref, args={"to": "a@b.com", "subject": "Hi", "body": "Hello"})
```

### Signature

```python
async def enqueue(
    target,             # Callable, string task name, or TaskRef
    *,
    args: dict | None = None,  # Function args (string / TaskRef shapes)
    priority: int = None,      # Override the job's default priority
    queue: str = None,         # Override the job's default queue
    scheduled_at: datetime = None,  # Run at a specific time (UTC)
    unique: bool = None,       # Override the job's default uniqueness
    dedup_key: str = None,     # Custom deduplication key (instead of args hash)
    connection = None,         # Asyncpg connection for transactional enqueue
    **func_kwargs,             # Function args (callable shape)
) -> str                       # Returns job UUID
```

`target` is the first positional argument and selects the input shape:

- **Callable**: function args travel as `**func_kwargs`. Don't pass `args=`.
- **String task name**: function args travel in `args=dict`. Don't pass `**func_kwargs` (they would collide with enqueue options).
- **`TaskRef`**: function args travel in `args=dict` and are validated against `ref.args_model` if set.

All option parameters are optional. When omitted, the values from the `@app.job` registration apply (or system defaults if no local registration).

### Transactional enqueue

Pass a `connection` to enqueue a job inside an existing database transaction.
If the transaction rolls back, the job is never created.

```python
await app.ensure_initialized()
async with app.backend.acquire() as conn:
    async with conn.transaction():
        await conn.execute("INSERT INTO orders (id) VALUES ($1)", order_id)
        await app.enqueue(fulfill_order, connection=conn, order_id=order_id)
```

Transactional enqueue requires the PostgreSQL backend.


## enqueue_many()

Bulk-enqueue many jobs that share the same target. Returns a list of job IDs in input order.

```python
ids = await app.enqueue_many(
    send_email,
    [
        {"to": "a@example.com", "subject": "Hi", "body": "Hello"},
        {"to": "b@example.com", "subject": "Hi", "body": "Hello"},
        {"to": "c@example.com", "subject": "Hi", "body": "Hello"},
    ],
    queue="emails",   # optional, applies to all rows
    priority=50,      # optional, applies to all rows
)
```

### Signature

```python
async def enqueue_many(
    target,                         # Callable, string task name, or TaskRef
    args_list: list[dict],          # One args dict per job
    *,
    queue: str | None = None,       # Shared override for all rows
    priority: int | None = None,    # Shared override for all rows
    scheduled_at = None,            # Shared override for all rows
) -> list[str]
```

On Postgres this issues one batched INSERT round-trip; on SQLite/Memory it loops over `create_job`. Each `args_list[i]` is validated against the registered `args_model` (if any) before any row is written.

`enqueue_many()` does not support `unique=True` or `dedup_key=`. If the registered job declares `unique=True`, or you need per-row dedup, call `enqueue()` in a loop instead.


## schedule()

Schedule a job for future execution.

```python
from datetime import datetime, timezone

# Absolute UTC datetime
await app.schedule(
    send_report,
    run_at=datetime(2025, 1, 1, 9, 0, tzinfo=timezone.utc),
    user_id=42,
)
```

```python
async def schedule(
    target,
    run_at,            # UTC datetime, or seconds-from-now (int/float)
    *,
    args: dict | None = None,
    **kwargs,
) -> str  # Returns job UUID
```

`app.schedule()` is a thin wrapper around `app.enqueue()` that sets `scheduled_at=run_at`.


## @app.periodic()

Declares a job that runs on a recurring schedule. Single decorator: registers the
function as a regular `@app.job` and stamps the schedule on it. The scheduler
process (`soniq scheduler`) picks up all `@periodic` functions automatically.

```python
from datetime import timedelta
from soniq import cron, daily, every

@app.periodic(cron=daily().at("09:00"), name="reports.daily")
async def daily_report():
    ...

@app.periodic(cron=every(10).minutes(), queue="maintenance", name="cleanup")
async def cleanup_old_sessions():
    ...

@app.periodic(every=timedelta(seconds=30), name="metrics.flush")
async def flush_metrics():
    ...
```

### Parameters

| Parameter | Type | Description |
|---|---|---|
| `cron` | `str` or builder | A 5-field cron expression, or any object whose `__str__` is one (e.g. `daily().at("09:00")` from `soniq.schedules`). |
| `every` | `timedelta` or `int`/`float` | Interval between runs. Use `timedelta` for clarity; ints are treated as seconds. |
| `**job_kwargs` | | Any parameter accepted by `@app.job` (`name`, `queue`, `priority`, `retries`, etc.). `name` is optional and falls back to `f"{module}.{qualname}"`. |

Rules:
- Specify exactly one of `cron=` or `every=`.
- They cannot be combined.

Requires a running `soniq scheduler` process to actually fire the jobs.


## JobContext

Runtime metadata injected into your job function. Declare a parameter with
type annotation `JobContext` and Soniq fills it in automatically.

```python
from soniq import JobContext

@app.job()
async def process_order(order_id: int, ctx: JobContext):
    print(f"Job {ctx.job_id}, attempt {ctx.attempt} of {ctx.max_attempts}")
```

### Attributes

| Attribute | Type | Description |
|---|---|---|
| `job_id` | `str` | UUID of this job. |
| `job_name` | `str` | Fully qualified name (`module.function`). |
| `attempt` | `int` | Current attempt number (starts at 1). |
| `max_attempts` | `int` | Total allowed attempts (`retries + 1`). |
| `queue` | `str` | Queue this job is running in. |
| `worker_id` | `str \| None` | UUID of the worker processing this job. |
| `scheduled_at` | `datetime \| None` | When the job was scheduled to run, if it was delayed. |
| `created_at` | `datetime \| None` | When the job was created. |

`JobContext` is a frozen dataclass. It is read-only.


## JobStatus

Enum of the lifecycle states a `soniq_jobs` row can hold.

```python
from soniq import JobStatus
```

| Value | Meaning |
|---|---|
| `JobStatus.QUEUED` | Waiting to be picked up by a worker. Retries also re-enter this state. |
| `JobStatus.PROCESSING` | Currently being executed. |
| `JobStatus.DONE` | Completed successfully. |
| `JobStatus.CANCELLED` | Cancelled before execution. |

Jobs that exhaust all retries are moved into the `soniq_dead_letter_jobs`
table; they do not remain in `soniq_jobs`. See
[Dead-letter queue](../reference/dead-letter.md).


## Imperative scheduling: `app.scheduler`

For schedules computed at runtime (per-tenant, per-flag, ...) use the
`Scheduler` service exposed on the Soniq instance:

```python
await app.scheduler.add(
    target=cleanup,        # callable, task-name string, or pass name=
    cron="0 9 * * *",      # OR: every=timedelta(...)
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

Schedules are keyed by the resolved task name. Re-adding the same name
updates the schedule in place rather than creating a duplicate.

## Cron-string DSL

`soniq.schedules` (also re-exported from the `soniq` package root) is a
small, pure-Python builder layer that returns plain cron strings:

```python
from datetime import timedelta
from soniq import cron, daily, every, monthly, weekly

every(5).minutes()                 # "*/5 * * * *"
every(2).hours()                   # "0 */2 * * *"
every(30).seconds()                # timedelta(seconds=30)
daily().at("09:00")                # "0 9 * * *"
weekly().on("monday").at("09:00")  # "0 9 * * 1"
monthly().on_day(15).at("12:00")   # "0 12 15 * *"
cron("*/15 * * * *")               # identity passthrough
```

Each terminal returns a `str`, so `cron=daily().at("09:00")` plugs straight
into `@app.periodic(cron=...)` or `app.scheduler.add(cron=...)`.
