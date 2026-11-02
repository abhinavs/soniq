---
description: Run Soniq workers for Python background jobs on PostgreSQL. Covers the soniq worker command, run_worker(), worker settings, LISTEN/NOTIFY and graceful shutdown.
---

# Run Soniq workers

A Soniq worker claims jobs from PostgreSQL, runs your handlers and records the result. Run workers with the `soniq worker` command from a process manager such as systemd, Kubernetes or supervisord. The manager restarts and scales them. The `Worker` class and `soniq.run_worker()` are documented here for advanced use cases (tests, embedding, custom orchestration).


## soniq worker

```bash
soniq worker myapp.jobs:soniq  # import the module and use that Soniq instance
soniq worker myapp.jobs:soniq --concurrency 8 --queues emails,billing
soniq worker myapp.jobs:soniq --run-once  # process available jobs once and exit
SONIQ_JOBS_MODULES=myapp.jobs soniq worker  # no app path: name the job modules
```

The command reads `SONIQ_DATABASE_URL` and the other `SONIQ_*` settings from the environment. Without an app path, the command exits with an error unless `SONIQ_JOBS_MODULES` or `--jobs-modules` names the job modules.

| Flag | Default | Description |
|---|---|---|
| `APP` (positional) | none | `module:attribute` of your `Soniq` instance. |
| `--concurrency` | `4` | Number of concurrent job tasks. |
| `--queues` | all queues | Comma-separated queue names. |
| `--jobs-modules` | none | Extra modules to import. Soniq merges them with `SONIQ_JOBS_MODULES`. |
| `--run-once` | off | Process available jobs and exit. |
| `--log-level` | `INFO` | Root logger level. Also read from `SONIQ_LOG_LEVEL`. |
| `--database-url` | none | Overrides the instance's database URL. |

See the [CLI reference](../cli/commands.md) for all commands.


## run_worker()

The in-process entry point. Use this for tests or when embedding Soniq inside a larger Python application that owns the lifecycle.

```python
soniq = Soniq(database_url="postgresql://localhost/myapp")

await soniq.run_worker(
    concurrency=4,
    run_once=False,
    queues=None,
    shutdown_event=None,
)
```

### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `concurrency` | `int` | `4` | Number of concurrent asyncio tasks fetching and executing jobs. |
| `run_once` | `bool` | `False` | Process all available jobs and exit. Useful for testing and cron-driven setups. |
| `queues` | `list[str] \| None` | `None` | Restrict to these queue names. `None` means process all queues. This is the in-process default; the `soniq worker` CLI also defaults to all queues when `--queues` is omitted. |
| `shutdown_event` | `asyncio.Event \| None` | `None` | Set it to shut the worker down and drain in-flight jobs. When you pass one, the worker installs no signal handlers. Ignored when `run_once` is `True`. |

On PostgreSQL, `run_worker()` raises `SoniqError` with code `SONIQ_POOL_TOO_SMALL` when `concurrency + pool_headroom` is greater than `pool_max_size`. Raise `pool_max_size` or lower `concurrency`.

### What happens during run_worker

1. The instance auto-initializes if needed (connects to the database, runs lazy setup).
2. A `Worker` instance is created with the instance's backend, job registry, settings, and hooks.
3. In continuous mode, the worker registers itself in the database, starts a heartbeat loop, subscribes to `LISTEN/NOTIFY` for instant job pickup, and launches `concurrency` processing tasks.
4. In `run_once` mode, the worker processes available jobs sequentially until the queue is empty, then returns.


## Worker configuration via environment variables

These environment variables control worker behavior when using the CLI or default
settings:

| Env var | Default | Description |
|---|---|---|
| `SONIQ_POLL_INTERVAL` | `5.0` | Seconds to wait when no jobs are available before polling again. Also the `LISTEN/NOTIFY` timeout. |
| `SONIQ_HEARTBEAT_INTERVAL` | `5.0` | Seconds between heartbeat updates. |
| `SONIQ_HEARTBEAT_TIMEOUT` | `300.0` | Seconds after which a worker with no heartbeat is considered stale. |
| `SONIQ_CLEANUP_INTERVAL` | `300.0` | Seconds between expired-job and stale-worker cleanup runs. |
| `SONIQ_ERROR_RETRY_DELAY` | `5.0` | Seconds to sleep after an unexpected worker-level error before resuming. |
| `SONIQ_SHUTDOWN_TIMEOUT` | `30.0` | Seconds that running jobs get to finish during shutdown. |
| `SONIQ_SYNC_HANDLER_POOL_SIZE` | `8` | Threads that run sync (non-async) handlers. |
| `SONIQ_JOBS_MODULES` | (empty) | Comma-separated list of modules to import on worker startup. The CLI needs it unless you pass an app path (`soniq worker myapp.jobs:soniq`), which Soniq imports for you. See [Job module discovery](../getting-started/installation.md#job-module-discovery). |

!!! note "Concurrency and queue selection"
    Soniq does not read `SONIQ_CONCURRENCY` or `SONIQ_QUEUES` when it starts a worker. The `soniq worker` command uses `--concurrency` (default 4) and processes all queues unless you pass `--queues name1,name2`.


## Worker class (advanced)

Most users never instantiate `Worker` directly. It is documented here for
contributors and users who need fine-grained control.

```python
from soniq.core.worker import Worker
from soniq.core.registry import JobRegistry
from soniq.settings import SoniqSettings

worker = Worker(
    backend=backend,       # A StorageBackend instance
    registry=registry,     # A JobRegistry with registered jobs
    settings=settings,     # SoniqSettings (optional, uses global defaults)
    hooks=hooks,           # Dict of hook lists (optional)
    middleware=None,       # List of middleware (optional)
)
```

### Constructor parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `backend` | `StorageBackend` | (required) | The storage backend (PostgresBackend, SQLiteBackend, or MemoryBackend). |
| `registry` | `JobRegistry` | (required) | Job registry containing all `@soniq.job()` registrations. |
| `settings` | `SoniqSettings \| None` | `None` | Settings instance. Falls back to global settings when `None`. |
| `hooks` | `dict \| None` | `None` | Hook dictionary with keys `"before_job"`, `"after_job"`, `"on_error"`, each mapping to a list of callables. |
| `middleware` | `list \| None` | `None` | Middleware callables, outermost first. |
| `retry_policy` | `RetryPolicy \| None` | `None` | Computes the delay before each retry. |
| `metrics_sink` | `MetricsSink \| None` | `None` | Receives job start and end events. |
| `sync_executor` | `ThreadPoolExecutor \| None` | `None` | Thread pool for sync handlers. A `Soniq` instance supplies it. |
| `sync_pool_semaphore` | `asyncio.Semaphore \| None` | `None` | Limits claimed sync jobs. A `Soniq` instance supplies it. |

### run()

```python
async def run(
    self,
    concurrency: int = 4,
    run_once: bool = False,
    queues: list[str] | None = None,
    shutdown_event: asyncio.Event | None = None,
) -> bool
```

Returns `True` if any jobs were processed. `shutdown_event` is ignored when `run_once` is `True`.

### run_once()

```python
async def run_once(
    self,
    queues: list[str] | None = None,
    max_jobs: int | None = None,
) -> bool
```

Process available jobs and return. Pass `max_jobs` to cap how many jobs are
processed in one call.


## Graceful shutdown

In continuous mode, the worker installs signal handlers for `SIGINT`, `SIGTERM` and `SIGHUP`.
On receiving either signal:

1. The shutdown event is set and the worker stops taking new jobs.
2. Running jobs get up to `shutdown_timeout` seconds (default 30) to finish.
3. Async jobs still running after that are cancelled and put back in the queue with their attempt count kept. Sync handlers are not cancelled. They get an extra `sync_handler_grace_seconds` (default: the `job_timeout` value), and after that the worker keeps waiting for the thread.
4. The worker deregisters itself from the database.
5. The `LISTEN/NOTIFY` connection is released.

A second signal does not force an exit. To stop a stuck worker, send `SIGKILL`. Jobs run at least once, so make handlers idempotent.


## LISTEN/NOTIFY

When the backend supports push notifications (PostgreSQL), the worker subscribes
to the `soniq_new_job` channel. When a job is enqueued, the worker wakes up
immediately instead of waiting for the next poll cycle. This keeps latency low
without hammering the database with frequent polls.

If `LISTEN/NOTIFY` setup fails (for example behind PgBouncer in transaction mode),
the worker falls back to polling at `poll_interval`.
