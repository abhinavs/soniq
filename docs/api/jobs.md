---
description: Define, enqueue and schedule Python background jobs with Soniq on PostgreSQL. Covers @soniq.job(), enqueue(), schedule(), @soniq.periodic() and Snooze.
---

# Define, enqueue and schedule jobs

A job in Soniq is an async Python function that you register with `@soniq.job()`. Soniq stores each call to `enqueue()` as a row in PostgreSQL, and a worker runs the function later. This page covers defining, enqueueing, scheduling and inspecting jobs.

## @soniq.job decorator

Registers a function as a job. Always called with parentheses, even with no kwargs.

```python
soniq = Soniq(database_url="postgresql://localhost/myapp")

@soniq.job(name="emails.send_email")
async def send_email(to: str, subject: str, body: str):
    ...

@soniq.job(name="emails.send_password_reset", retries=5, queue="urgent")
async def send_password_reset(to: str, token: str):
    ...
```

### Parameters

All parameters are optional.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `name` | `str \| None` | `None` | Task name stored in the job's row. Always pass it. The name must match the `task_name_pattern` setting, which by default means lowercase dotted segments such as `emails.send_email`. When omitted, Soniq derives the name from `f"{module}.{function_name}"`. This is deprecated since 0.2.0 and warns. See [Storage format](../reference/storage-format.md#job-names). |
| `retries` | `int` | `3` | Maximum retries after the first failure. A job runs at most `retries + 1` times. Alias: `max_retries`. |
| `priority` | `int` | `100` | A lower number runs first. |
| `queue` | `str` | `"default"` | Queue name for this job. |
| `unique` | `bool` | `False` | Deduplicate by job name and argument hash. If a matching job is still `queued`, `enqueue()` creates no job and returns the id of the existing one. |
| `retry_delay` | `int \| float \| list[int \| float]` | `0` | Seconds to wait before each retry. Pass a list to set per-attempt delays (e.g. `[1, 5, 30]`). |
| `retry_backoff` | `bool` | `False` | Double the delay on each attempt. The base is `retry_delay`, or 1 second when `retry_delay` is `0`. |
| `retry_max_delay` | `int \| float \| None` | `None` | Cap on retry delay in seconds. |
| `retry_jitter` | `bool` | `True` | With `retry_backoff`, pick a random delay between half of the backoff delay and the full delay. Ignored without `retry_backoff`. |
| `timeout` | `int \| float \| None` | `None` | Per-job timeout in seconds. `None` uses the `job_timeout` setting (default 300 seconds). A timeout counts as a failure and follows the retry rules. |
| `validate` | `type[BaseModel] \| None` | `None` | Pydantic model that checks the job arguments at enqueue time. Alias: `args_model`. |

```python
@soniq.job(
    name="payments.process_payment",
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
# 1. Callable (same codebase)
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
    target,             # Callable, string task name, or TaskRef (positional only)
    /,
    *,
    args: dict | None = None,  # Function args (string / TaskRef shapes)
    priority: int | None = None,     # Override the job's default priority
    queue: str | None = None,        # Override the job's default queue
    scheduled_at: datetime | timedelta | int | float | None = None,  # Run at a time, or after a delay
    unique: bool | None = None,      # Override the job's default uniqueness
    dedup_key: str | None = None,    # Custom deduplication key (instead of args hash)
    connection = None,               # Connection for transactional enqueue
    **func_kwargs,                   # Function args (callable shape)
) -> str                             # Returns the job UUID
```

`target` is the first positional argument and selects the input shape:

- **Callable**: function args travel as `**func_kwargs`. Don't pass `args=`.
- **String task name**: function args travel in `args=dict`. Don't pass `**func_kwargs` (they would collide with enqueue options).
- **`TaskRef`**: function args travel in `args=dict` and are validated against `ref.args_model` if set.

All option parameters are optional. When you omit one, the value from the `@soniq.job()` registration applies. A job with no local registration gets priority 100, queue `default` and `max_retries + 1` attempts (from the `max_retries` setting).

`scheduled_at` accepts a timezone-aware `datetime`, a `timedelta`, or a number of seconds from now. A naive `datetime` raises `ValueError`.

`enqueue()` rejects bad arguments before it writes a row. It raises `SoniqError` with code `SONIQ_TASK_ARGS_INVALID` when an argument is not JSON-serializable, does not fit the handler signature, or fails the `validate=` model. It raises `SONIQ_UNKNOWN_TASK_NAME` when you enqueue an unregistered string name and `enqueue_validation` is `"strict"` (the default).

### Transactional enqueue

Pass a `connection` to enqueue a job inside an existing database transaction.
If the transaction rolls back, the job is never created. The connection must have an active transaction. Soniq accepts an `asyncpg` connection, a SQLAlchemy `AsyncSession` or `AsyncConnection`, and a `psycopg` `AsyncConnection`.

```python
await soniq.ensure_initialized()
async with soniq.backend.acquire() as conn:
    async with conn.transaction():
        await conn.execute("INSERT INTO orders (id) VALUES ($1)", order_id)
        await soniq.enqueue(fulfill_order, connection=conn, order_id=order_id)
```

Transactional enqueue requires the PostgreSQL backend. See [Transactional enqueue](../guides/transactional-enqueue.md) for the other connection types.


## job.enqueue() and job.options()

The decorator adds two methods to the job function: `.enqueue()` and `.options()`. Use them to enqueue a job without passing the function to `soniq.enqueue()`.

```python
soniq = Soniq(database_url="postgresql://localhost/myapp")

@soniq.job(name="emails.send_welcome")
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

On PostgreSQL, `enqueue_many()` writes all rows with one batched INSERT. On SQLite and the in-memory backend, it inserts the rows one by one. Soniq checks every `args_list[i]` before it writes any row, so one bad item fails the whole call.

`enqueue_many()` has no `unique=` or `dedup_key=` option. If the registered job declares `unique=True`, `enqueue_many()` raises `TypeError`. For per-row deduplication, call `enqueue()` in a loop. An empty `args_list` returns `[]`.


## schedule()

Schedule a job to run at a later time. `schedule()` is `enqueue()` with `scheduled_at=run_at`.

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
    run_at,            # Timezone-aware datetime, timedelta, or seconds from now (int/float)
    *,
    args: dict | None = None,
    **kwargs,
) -> str  # Returns job UUID
```

`soniq.schedule()` passes `**kwargs` to `enqueue()`, so `kwargs` are job arguments for the callable shape. A naive `datetime` raises `ValueError`. For a recurring schedule, use `@soniq.periodic()`.


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

The scheduler checks for due jobs every `--check-interval` seconds (default 60). If an `every=` interval is shorter, the scheduler lowers its check interval to match at startup, so the 30-second job above fires every 30 seconds. The first run comes about one check interval after the scheduler starts.

### Parameters

| Parameter | Type | Description |
|---|---|---|
| `cron` | `str` or builder | A 5-field cron expression, or any object whose `__str__` is one (e.g. `daily().at("09:00")` from `soniq.schedules`). Soniq validates the expression when the decorator runs. |
| `every` | `timedelta` or `int`/`float` | Interval between runs. Use `timedelta` for clarity; numbers are seconds. The minimum is 1 second. |
| `**job_kwargs` | | Any parameter accepted by `@soniq.job` (`name`, `queue`, `priority`, `retries`, etc.). Always pass `name`: without it the name falls back to `f"{module}.{function_name}"`, which is deprecated since 0.2.0 and warns. |

Rules:
- Specify exactly one of `cron=` or `every=`.
- They cannot be combined.

Requires a running `soniq scheduler` process to actually fire the jobs.


## Snooze

A handler returns `Snooze` to run again later without using a retry. Use it when the job must wait for something outside Soniq, such as a rate limit.

```python
from soniq import Snooze

@soniq.job(name="sync.pull_orders")
async def pull_orders():
    if rate_limited():
        return Snooze(seconds=60, reason="rate limited")
```

`Snooze(seconds, reason=None)` puts the job back in `queued` with a new run time. The attempt count does not go up. The `snooze_max_seconds` setting caps the delay (default 86400 seconds). Soniq fires no `after_job` or `on_error` hook for a snoozed run.


## JobContext

Runtime metadata injected into your job function. Declare a parameter with
type annotation `JobContext` and Soniq fills it in automatically.

```python
from soniq import JobContext

@soniq.job(name="orders.process_order")
async def process_order(order_id: int, ctx: JobContext):
    print(f"Job {ctx.job_id}, attempt {ctx.attempt} of {ctx.max_attempts}")
```

### Attributes

| Attribute | Type | Description |
|---|---|---|
| `job_id` | `str` | UUID of this job. |
| `job_name` | `str` | The registered job name (the `name=` you gave the decorator). |
| `attempt` | `int` | Current attempt number (starts at 1). |
| `max_attempts` | `int` | Total allowed attempts (`retries + 1`). |
| `queue` | `str` | Queue this job is running in. |
| `worker_id` | `str` | UUID of the worker processing this job. Empty string when the context is built outside a worker claim. |
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
    max_attempts=3,
)

await soniq.scheduler.pause("reports.daily")
await soniq.scheduler.resume("reports.daily")
await soniq.scheduler.remove("reports.daily")
schedules = await soniq.scheduler.list(status="active")
sched = await soniq.scheduler.get("reports.daily")
```

`add()` takes `target`, `cron`, `every`, `args`, `queue`, `priority`, `max_attempts` and `name`. Pass exactly one of `cron` or `every`. It returns the schedule name.

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
every(2).days()                    # "0 0 */2 * *"
daily().at("09:00")                # "0 9 * * *"
weekly().on("monday").at("09:00")  # "0 9 * * 1"
monthly().on_day(15).at("12:00")   # "0 12 15 * *"
cron("*/15 * * * *")               # identity passthrough
```

Each terminal returns a `str`, so `cron=daily().at("09:00")` plugs straight
into `@soniq.periodic(cron=...)` or `soniq.scheduler.add(cron=...)`.
