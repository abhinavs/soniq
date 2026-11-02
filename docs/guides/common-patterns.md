---
description: Soniq patterns for Python background jobs on PostgreSQL - hooks, JobContext, deduplication with unique and dedup_key, and job results.
---

# Common Soniq patterns: hooks, deduplication and job results

This page shows the Soniq patterns most applications use: job hooks, `JobContext`, deduplication, job results, and the async context manager. Each pattern has a short, runnable example.

## Job hooks: run code before, after and on error

Hooks run before a job, after a job succeeds, or when a job fails. Use them for logging, metrics and error reporting. A hook can be sync or async. A hook that raises is logged and ignored, so it never blocks the job.

```python
from soniq import Soniq

soniq = Soniq(database_url="postgresql://localhost/myapp")


@soniq.before_job
async def log_start(job_name, job_id, attempt):
    print(f"Starting {job_name} (attempt {attempt})")


@soniq.after_job
async def log_complete(job_name, job_id, duration_ms):
    print(f"Completed {job_name} in {duration_ms}ms")


@soniq.on_error
async def log_failure(job_name, job_id, error, attempt):
    print(f"Failed {job_name}: {error}")
```

Soniq passes the arguments to a hook by position, in this order:

| Hook | Runs | Arguments |
| --- | --- | --- |
| `@soniq.before_job` | Before each attempt | `job_name`, `job_id`, `attempt` |
| `@soniq.after_job` | After a job succeeds | `job_name`, `job_id`, `duration_ms` |
| `@soniq.on_error` | After an attempt raises | `job_name`, `job_id`, `error` (a string), `attempt` |

You can register more than one hook of each type. They run in registration order.

Hooks observe a job but cannot wrap it. To set up state that the handler needs, such as a tracing span or a `ContextVar`, register a middleware with `@soniq.middleware`. A middleware is an async function that takes `ctx` and `call_next`. It calls `await call_next(ctx)` to run the rest of the chain and returns the result.

## JobContext

Job functions can receive runtime metadata by adding a `ctx: JobContext` parameter:

```python
from soniq import JobContext

@soniq.job(name="orders.process_order", queue="default")
async def process_order(order_id: int, ctx: JobContext):
    print(f"Job {ctx.job_id}, attempt {ctx.attempt} of {ctx.max_attempts}")
    print(f"Queue: {ctx.queue}, Worker: {ctx.worker_id}")
```

Soniq injects the context when it sees the `JobContext` type annotation. The parameter name does not matter, but `ctx` is the convention. Do not pass `ctx` when you enqueue the job.

Available fields:

| Field | Type | Description |
| --- | --- | --- |
| `job_id` | `str` | Unique job UUID |
| `job_name` | `str` | Registered job name |
| `attempt` | `int` | Current attempt number (starts at 1) |
| `max_attempts` | `int` | Maximum attempts before dead-lettering |
| `queue` | `str` | Queue the job was enqueued to |
| `worker_id` | `str` | ID of the worker processing this job |
| `scheduled_at` | `datetime` or `None` | When the job was scheduled to run. `None` for jobs queued to run at once |
| `created_at` | `datetime` or `None` | When the job was created |

## Deduplication

Prevent duplicate jobs with `unique=True` or a custom `dedup_key`. A duplicate is detected only while the first job is still `queued`. After a worker claims the job, the same enqueue creates a new job.

### Argument-based deduplication

With `unique=True`, Soniq hashes the job arguments. If a queued job with the same name and arguments exists, `enqueue()` creates no new job and returns the id of the existing job:

```python
@soniq.job(name="users.sync_user", unique=True)
async def sync_user(user_id: int):
    ...

# First call creates the job
await soniq.enqueue(sync_user, user_id=42)

# Second call with the same args returns the id of the first job
await soniq.enqueue(sync_user, user_id=42)

# Different args creates a new job
await soniq.enqueue(sync_user, user_id=99)
```

### Custom dedup key

For more control, pass a `dedup_key` at enqueue time:

```python
await soniq.enqueue(
    sync_user,
    user_id=42,
    dedup_key="sync-user-42",
)
```

The key lets you deduplicate across different arguments, or use a key that has meaning in your business. A queued job with the same `dedup_key` makes `enqueue()` return the existing job id.

## Job results

Job functions can return values. Retrieve them after the job completes:

```python
@soniq.job(name="jobs.add")
async def add(a: int, b: int):
    return a + b

job_id = await soniq.enqueue(add, a=2, b=3)

# ... after the worker processes it ...

result = await soniq.get_result(job_id)
# result == 5
```

`get_result()` returns `None` if the job is not done or does not exist. To read the full job record, call `get_job()`:

```python
job = await soniq.get_job(job_id)
# {"status": "done", "result": 5, "job_name": "...", ...}
```

Soniq stores results as JSON in the database, and the return value must be JSON-serializable. For large results, store the data elsewhere (for example S3) and return a reference.

## Async context manager

In scripts, migrations and one-off tasks, use the async context manager. It sets up Soniq on entry and closes it on exit:

```python
async with Soniq(database_url="postgresql://localhost/myapp") as soniq:
    @soniq.job(name="jobs.my_task")
    async def my_task(value: str):
        print(value)

    await soniq.enqueue(my_task, value="hello")
    await soniq.run_worker(run_once=True)
```

The context manager calls `close()` on exit.

Use it for:

- Database migration scripts that enqueue follow-up jobs
- One-shot CLI tools
- Integration tests that need a real PostgreSQL backend

## Next steps

- [Testing](testing.md) - test hooks and deduplication
- [Transactional enqueue](transactional-enqueue.md) - enqueue a job in the same transaction as your data
