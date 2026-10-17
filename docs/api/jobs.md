# Jobs

Everything about defining, enqueueing, scheduling, and inspecting jobs.


## @soniq.job decorator

Registers a function as a job. Always called with parentheses, even with no kwargs.

```python
soniq = Soniq(database_url="postgresql://localhost/myapp")

@soniq.job()
async def send_email(to: str, subject: str, body: str):
    ...

@soniq.job(retries=5, queue="urgent")
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
@soniq.job(
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
job_id = await soniq.enqueue(send_email, to="a@b.com", subject="Hi", body="Hello")

# 2. String task name (cross-service / by-name)
job_id = await soniq.enqueue(
    "users.send_email",
    args={"to": "a@b.com", "subject": "Hi", "body": "Hello"},
)

# 3. TaskRef (typed cross-repo stub)
job_id = await soniq.enqueue(send_email_ref, args={"to": "a@b.com", "subject": "Hi", "body": "Hello"})
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

All option parameters are optional. When omitted, the values from the `@soniq.job` registration apply (or system defaults if no local registration).

### Transactional enqueue

Pass a `connection` to enqueue a job inside an existing database transaction.
If the transaction rolls back, the job is never created.

```python
await soniq.ensure_initialized()
async with soniq.backend.acquire() as conn:
    async with conn.transaction():
        await conn.execute("INSERT INTO orders (id) VALUES ($1)", order_id)
        await soniq.enqueue(fulfill_order, connection=conn, order_id=order_id)
```

Transactional enqueue requires the PostgreSQL backend.


## job.enqueue() and job.options()

The decorator adds two methods to the job function: `.enqueue()` and `.options()`. Use them to enqueue a job without passing the function to `soniq.enqueue()`.

```python
soniq = Soniq(database_url="postgresql://localhost/myapp")

@soniq.job()
async def send_welcome(to: str) -> None:
    ...

# Enqueue with the job's own arguments.
job_id = await send_welcome.enqueue(to="dev@example.com")

# Set enqueue options, then enqueue.
job_id = await send_welcome.options(queue="emails", priority=10).enqueue(
    to="dev@example.com"
)

# The function is still a plain async function.
await send_welcome(to="dev@example.com")
```

`send_welcome.enqueue(**kwargs)` does the same thing as `soniq.enqueue(send_welcome, **kwargs)`. It uses the `Soniq` instance that registered the job, and the same task name, also when you set `name=` on the decorator.

`.options()` accepts the enqueue options of `enqueue()`: `queue`, `priority`, `scheduled_at`, `unique`, `dedup_key` and `connection`. It returns an object with one method, `.enqueue(**kwargs)`. You can keep this object and use it more than one time.

```python
async with soniq.backend.acquire() as conn:
    async with conn.transaction():
        await conn.execute("INSERT INTO users (email) VALUES ($1)", email)
        await send_welcome.options(connection=conn).enqueue(to=email)
```

Use `.options()` when the job has an argument with the same name as an enqueue option, for example `queue`. In `send_welcome.enqueue(queue="x")`, `queue` is the enqueue option, not the job argument. This is the same rule as for `soniq.enqueue()`.

### Type checking

A type checker such as mypy or pyright checks the arguments of `.enqueue()` against the job signature. A wrong argument name or type is an error before the code runs:

```python
await send_welcome.enqueue(too="dev@example.com")  # error: unexpected keyword "too"
```

The static return type of `@soniq.job()` is `soniq.types.JobFunction[P, R]`. It is a typing aid only. At runtime the decorator returns the same plain function as before, with the extra attributes. A `JobFunction` is still a `Callable[P, Awaitable[R]]`, so code that wraps or calls the job type-checks without changes.

Pass job arguments as keywords. A positional argument to `.enqueue()` raises `TypeError`. A type checker does not report it.

### Compatibility

`soniq.enqueue(send_welcome, ...)` stays supported. It is not deprecated, and there is no plan to remove it. Both forms write the same job row. Use the form that you prefer.

If your function already has an attribute named `enqueue` or `options`, Soniq does not replace it. Soniq writes a debug log message and does not add that method.


## enqueue_many()

Bulk-enqueue many jobs that share the same target. Returns a list of job IDs in input order.

```python
ids = await soniq.enqueue_many(
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
await soniq.schedule(
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

`soniq.schedule()` is a thin wrapper around `soniq.enqueue()` that sets `scheduled_at=run_at`.


## @soniq.periodic()

Declares a job that runs on a recurring schedule. Single decorator: registers the
function as a regular `@soniq.job` and stamps the schedule on it. The scheduler
process (`soniq scheduler`) picks up all `@periodic` functions automatically.

```python
from datetime import timedelta
from soniq import cron, daily, every

@soniq.periodic(cron=daily().at("09:00"), name="reports.daily")
async def daily_report():
    ...

@soniq.periodic(cron=every(10).minutes(), queue="maintenance", name="cleanup")
async def cleanup_old_sessions():
    ...

@soniq.periodic(every=timedelta(seconds=30), name="metrics.flush")
async def flush_metrics():
    ...
```

The scheduler checks for due jobs every `--check-interval` seconds (default 60). An `every=` interval shorter than that rounds up to it, so the 30-second job above fires about once a minute unless you run `soniq scheduler --check-interval 30` or lower. The first run comes about one check interval after the scheduler starts.

### Parameters

| Parameter | Type | Description |
|---|---|---|
| `cron` | `str` or builder | A 5-field cron expression, or any object whose `__str__` is one (e.g. `daily().at("09:00")` from `soniq.schedules`). |
| `every` | `timedelta` or `int`/`float` | Interval between runs. Use `timedelta` for clarity; ints are treated as seconds. |
| `**job_kwargs` | | Any parameter accepted by `@soniq.job` (`name`, `queue`, `priority`, `retries`, etc.). `name` is optional and falls back to `f"{module}.{qualname}"`. |

Rules:
- Specify exactly one of `cron=` or `every=`.
- They cannot be combined.

Requires a running `soniq scheduler` process to actually fire the jobs.


## JobContext

Runtime metadata injected into your job function. Declare a parameter with
type annotation `JobContext` and Soniq fills it in automatically.

```python
from soniq import JobContext

@soniq.job()
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


## Imperative scheduling: `soniq.scheduler`

For schedules computed at runtime (per-tenant, per-flag, ...) use the
`Scheduler` service exposed on the Soniq instance:

```python
await soniq.scheduler.add(
    target=cleanup,        # callable, task-name string, or pass name=
    cron="0 9 * * *",      # OR: every=timedelta(...)
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
into `@soniq.periodic(cron=...)` or `soniq.scheduler.add(cron=...)`.
