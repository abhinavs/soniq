# Common Patterns

Patterns you'll reach for in most Soniq applications: middleware hooks, job context, deduplication, results, and the async context manager.

## Middleware hooks

Register functions that run before and after every job, or when a job fails. Useful for logging, metrics, tracing, and cleanup.

```python
from soniq import Soniq

eq = Soniq(database_url="postgresql://localhost/myapp")


@eq.before_job
async def log_start(ctx):
    print(f"Starting {ctx.job_name} (attempt {ctx.attempt})")


@eq.after_job
async def log_complete(ctx):
    print(f"Completed {ctx.job_name}")


@eq.on_error
async def log_failure(ctx, error):
    print(f"Failed {ctx.job_name}: {error}")
```

**`@app.before_job`** -- called before each job executes. Receives the `JobContext`. Use it for setting up tracing spans, logging, or injecting request-scoped state.

**`@app.after_job`** -- called after a job completes successfully. Receives the `JobContext`. Use it for recording metrics, cleaning up resources, or sending notifications.

**`@app.on_error`** -- called when a job raises an exception. Receives the `JobContext` and the exception. Use it for error reporting, alerting, or custom retry logic.

You can register multiple hooks of each type. They run in registration order.

## JobContext

Job functions can receive runtime metadata by adding a `ctx: JobContext` parameter:

```python
from soniq.job import JobContext

@eq.job(queue="default")
async def process_order(order_id: int, ctx: JobContext):
    print(f"Job {ctx.job_id}, attempt {ctx.attempt} of {ctx.max_attempts}")
    print(f"Queue: {ctx.queue}, Worker: {ctx.worker_id}")
```

Soniq injects the context automatically when it sees the type annotation. The parameter name doesn't matter, but `ctx` is conventional.

Available fields:

| Field | Type | Description |
| --- | --- | --- |
| `job_id` | `str` | Unique job UUID |
| `job_name` | `str` | Fully qualified function name |
| `attempt` | `int` | Current attempt number (starts at 1) |
| `max_attempts` | `int` | Maximum attempts before dead-lettering |
| `queue` | `str` | Queue the job was enqueued to |
| `worker_id` | `str` | ID of the worker processing this job |
| `scheduled_at` | `datetime` | When the job was scheduled to run |
| `created_at` | `datetime` | When the job was created |

## Deduplication

Prevent duplicate jobs with `unique=True` or a custom `dedup_key`.

### Argument-based deduplication

With `unique=True`, Soniq hashes the job arguments. If a job with the same name and arguments already exists in a non-terminal state, the enqueue is a no-op:

```python
@eq.job(unique=True)
async def sync_user(user_id: int):
    ...

# First call creates the job
await eq.enqueue(sync_user, user_id=42)

# Second call with same args is silently deduplicated
await eq.enqueue(sync_user, user_id=42)

# Different args creates a new job
await eq.enqueue(sync_user, user_id=99)
```

### Custom dedup key

For more control, pass a `dedup_key` at enqueue time:

```python
await eq.enqueue(
    sync_user,
    user_id=42,
    dedup_key="sync-user-42",
)
```

This lets you deduplicate across different argument combinations or use business-meaningful keys.

## Job results

Job functions can return values. Retrieve them after the job completes:

```python
@eq.job()
async def add(a: int, b: int):
    return a + b

job_id = await eq.enqueue(add, a=2, b=3)

# ... after the worker processes it ...

result = await eq.get_result(job_id)
# result == 5
```

`get_result()` returns `None` if the job hasn't completed yet or doesn't exist. Check the full job status with `get_job_status()`:

```python
status = await eq.get_job_status(job_id)
# {"status": "done", "result": 5, "job_name": "...", ...}
```

> **Tip:** Results are stored in the database. For large return values, store them externally (S3, Redis) and return a reference instead.

## Async context manager

For scripts, migrations, or one-off tasks, use the async context manager to handle setup and teardown automatically:

```python
async with Soniq(database_url="postgresql://localhost/myapp") as eq:
    @eq.job()
    async def my_task(value: str):
        print(value)

    await eq.enqueue(my_task, value="hello")
    await eq.run_worker(run_once=True)
```

The context manager calls `close()` on exit. The connection pool initializes lazily on first use.

This is handy for:

- Database migration scripts that need to enqueue follow-up jobs
- One-shot CLI tools
- Integration tests that need a real PostgreSQL backend
