---
description: Reference for the Soniq class, the Python entry point of the PostgreSQL job queue. Constructor, settings, lifecycle, job lookup, queue stats and env vars.
---

# Soniq class reference

The `Soniq` class is the central object in Soniq, a Python background-job library that stores jobs in PostgreSQL. An instance owns one database connection pool, one job registry and one set of settings. You can run several independent instances in the same process.

## Constructor

```python
from soniq import Soniq

soniq = Soniq(
    database_url="postgresql://localhost/myapp",
    backend=None,
    retry_policy=None,
    metrics_sink=None,
    plugins=None,
    autoload_plugins=False,
    **settings_overrides,
)
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `database_url` | `str \| None` | `None` | PostgreSQL connection URL. When `None`, Soniq reads `SONIQ_DATABASE_URL` and falls back to `postgresql://postgres@localhost/soniq`. A path ending in `.db`, `.sqlite` or `.sqlite3`, or a `sqlite:///` URL, selects the SQLite backend. |
| `backend` | `StorageBackend \| str \| None` | `None` | A backend instance, or one of `"postgres"`, `"sqlite"`, `"memory"`. When `None`, Soniq picks the backend from `database_url`. Any other string raises `ValueError`. |
| `retry_policy` | `RetryPolicy \| None` | `None` | Object with a `delay_for(attempt, job_meta, exc)` method. The default policy reads the retry options of each job. |
| `metrics_sink` | `MetricsSink \| None` | `None` | Receives job start and end events. See [Custom metrics sink](../recipes/custom-metrics-sink.md). |
| `plugins` | `list \| None` | `None` | Plugins to install when the instance is created. |
| `autoload_plugins` | `bool` | `False` | Install every plugin that Soniq discovers through entry points. |
| `**settings_overrides` | | | Any field of `SoniqSettings`, as described below. |

You can read `soniq.retry_policy` and `soniq.metrics_sink`, and assign new values to them. Assigning after the instance is initialized raises `SoniqError` with code `SONIQ_APP_FROZEN`.

### Settings

Pass a setting as a keyword argument, or set the matching `SONIQ_*` environment variable. Soniq validates every value when the instance is created.

| Keyword | Type | Default | Env var |
|---|---|---|---|
| `concurrency` | `int` (1-100) | `4` | `SONIQ_CONCURRENCY` |
| `max_retries` | `int` (0-10) | `3` | `SONIQ_MAX_RETRIES` |
| `priority` | `int` (1-1000) | `100` | `SONIQ_PRIORITY` |
| `queues` | `list[str]` | `["default"]` | `SONIQ_QUEUES` (comma-separated) |
| `result_ttl` | `int` | `300` | `SONIQ_RESULT_TTL` |
| `job_timeout` | `float \| None` | `300.0` | `SONIQ_JOB_TIMEOUT` (`0` disables the timeout) |
| `pool_min_size` | `int` | `5` | `SONIQ_POOL_MIN_SIZE` |
| `pool_max_size` | `int` | `20` | `SONIQ_POOL_MAX_SIZE` |
| `pool_headroom` | `int` | `2` | `SONIQ_POOL_HEADROOM` |
| `poll_interval` | `float` | `5.0` | `SONIQ_POLL_INTERVAL` |
| `heartbeat_interval` | `float` | `5.0` | `SONIQ_HEARTBEAT_INTERVAL` |
| `heartbeat_timeout` | `float` | `300.0` | `SONIQ_HEARTBEAT_TIMEOUT` |
| `cleanup_interval` | `float` | `300.0` | `SONIQ_CLEANUP_INTERVAL` |
| `error_retry_delay` | `float` | `5.0` | `SONIQ_ERROR_RETRY_DELAY` |
| `shutdown_timeout` | `float` | `30.0` | `SONIQ_SHUTDOWN_TIMEOUT` |
| `sync_handler_pool_size` | `int` | `8` | `SONIQ_SYNC_HANDLER_POOL_SIZE` |
| `sync_handler_grace_seconds` | `float \| None` | `None` (uses `job_timeout`) | `SONIQ_SYNC_HANDLER_GRACE_SECONDS` |
| `snooze_max_seconds` | `float` | `86400` | `SONIQ_SNOOZE_MAX_SECONDS` |
| `enqueue_validation` | `"strict" \| "warn" \| "none"` | `"strict"` | `SONIQ_ENQUEUE_VALIDATION` |
| `task_name_pattern` | `str` | `^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$` | `SONIQ_TASK_NAME_PATTERN` |
| `producer_id` | `str` | `"auto"` (`hostname:pid:argv0`) | `SONIQ_PRODUCER_ID` |
| `route_map` | `dict[str, str]` | `{}` | `SONIQ_ROUTE_MAP` (JSON object) |
| `jobs_modules` | `str` | `""` | `SONIQ_JOBS_MODULES` (comma-separated) |
| `log_level` | `str` | `"INFO"` | `SONIQ_LOG_LEVEL` |
| `debug` | `bool` | `False` | `SONIQ_DEBUG` |
| `environment` | `str` | `"production"` | `SONIQ_ENVIRONMENT` |

!!! note "Settings that Soniq does not apply for you"
    Soniq does not read `concurrency`, `queues` or `priority` when it runs a worker or enqueues a job. The `soniq worker` command and `run_worker()` default to 4 workers on all queues. Pass `--concurrency` and `--queues`, or the matching `run_worker()` arguments. A job gets its priority from its decorator, and a job with no local registration gets priority 100. The settings stay available as `soniq.settings.concurrency` for your own code.

`max_retries` applies only when you enqueue a job name that is not registered in the current process. A registered job uses its own `retries`.

### Optional services

Optional features have no `*_enabled` flags. A feature is on when your code uses its property on the instance. Each property builds its service on first access and caches it.

```python
await soniq.webhooks.register(url="https://...")      # webhooks
await soniq.dead_letter.list_dead_letter_jobs()       # dead-letter queue
await soniq.scheduler.add(my_job, cron="0 9 * * *")   # recurring schedules
soniq.signing.encrypt("plaintext")                    # Fernet encryption helpers
await soniq.logs.search_logs("error")                 # structured log queries
```

Other properties are `soniq.settings`, `soniq.registry` (the job registry), `soniq.backend` (`None` until the instance is initialized), `soniq.is_initialized` and `soniq.is_closed`.

The dashboard runs as its own process. Start it with `soniq dashboard`, or call `create_dashboard_app(soniq)`. HTTP write actions need `SONIQ_DASHBOARD_WRITE_ENABLED=true` and `SONIQ_DASHBOARD_API_KEY`.


## Lifecycle

The connection pool opens on first use, so most code needs no explicit init call. Call `await soniq.ensure_initialized()` when you need `soniq.backend` before the first enqueue.

### close()

`close()` shuts down plugins in reverse install order, then closes the connection pool. A second call logs a warning and does nothing else.

```python
await soniq.close()
```

### Async context manager

`async with` initializes the instance on entry and calls `close()` on exit:

```python
async with Soniq(database_url="postgresql://localhost/myapp") as soniq:
    await soniq.enqueue(my_job, message="hello")
```

### setup()

```python
applied = await soniq.setup()   # returns the number of migrations applied
```

`setup()` creates the database if it does not exist, runs the migrations and starts plugin `on_startup` hooks. This is the same work as the `soniq setup` command.

!!! warning "Run migrations once per deploy"
    Run `soniq setup` from your deploy pipeline. Do not run `setup()` from application code that starts on every replica, because replicas that start together race each other.


## Acquiring a connection

Use a pooled connection when you need raw SQL next to an enqueue, as in [transactional enqueue](../guides/transactional-enqueue.md):

```python
await soniq.ensure_initialized()
async with soniq.backend.acquire() as conn:
    async with conn.transaction():
        await conn.execute("INSERT INTO orders ...")
        await soniq.enqueue(send_receipt, connection=conn, order_id=42)
```

`backend.acquire()` is an async context manager. It lends a pooled connection and releases it on exit. Transactional enqueue needs the PostgreSQL backend.


## get_queue_stats()

Returns one dictionary with job counts for the whole instance. It is not split by queue.

```python
stats = await soniq.get_queue_stats()
# {"total": 150, "queued": 12, "processing": 3, "done": 130,
#  "dead_letter": 4, "cancelled": 1}
```

`queued`, `processing`, `done` and `cancelled` count rows in `soniq_jobs`. `dead_letter` counts rows in `soniq_dead_letter_jobs`. `total` is the sum of the five counts. When there are no jobs, every count is `0`.


## Job management methods

Each method takes a `job_id` string, which is the UUID that `enqueue()` returns. A string that is not a valid UUID raises `ValueError` on PostgreSQL.

| Method | Returns | Description |
|---|---|---|
| `get_job(job_id)` | `dict \| None` | The job record, or `None` if the id is unknown. A dead-lettered job comes back with `status` `"dead_letter"` and a `dead_letter_reason`. |
| `get_result(job_id, *, result_model=None)` | `Any \| None` | The return value of a job in status `done`. Returns `None` for any other status, or when the job stored no result. Pass a Pydantic model class as `result_model` to parse the value. |
| `cancel_job(job_id)` | `bool` | `True` if Soniq cancelled the job. Only a job in status `queued` can be cancelled. |
| `delete_job(job_id)` | `bool` | `True` if Soniq deleted the row from `soniq_jobs`. |
| `list_jobs(queue=None, status=None, limit=100, offset=0)` | `list[dict]` | Job records, filtered by queue and status. |

To run a job again after it exhausted its retries, call `soniq.dead_letter.replay(dead_letter_id)`. See the [dead-letter API](dead-letter.md).


## Configure Soniq with environment variables

Every setting has a `SONIQ_*` environment variable. Soniq resolves each value from the first source that sets it:

1. Keyword arguments passed to the constructor
2. Environment variables (`SONIQ_*`)
3. The `.env` file in the working directory
4. Default values

Example `.env` file:

```
SONIQ_DATABASE_URL=postgresql://user:pass@db.example.com/myapp
SONIQ_MAX_RETRIES=5
SONIQ_LOG_LEVEL=DEBUG
```

## Next steps

- [Jobs API](jobs.md) - define, enqueue and schedule jobs.
- [Worker API](worker.md) - run workers in a process.
- [Hooks API](hooks.md) - run code around every job.
