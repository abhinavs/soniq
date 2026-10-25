---
description: Run Soniq workers for Python background jobs on PostgreSQL. Covers soniq worker flags, concurrency, pool sizing, heartbeats, graceful shutdown and crash recovery.
---

# 2. Run Soniq workers: concurrency, shutdown and crash recovery

> **Beginner** - 8 minutes. Starting workers, concurrency, graceful shutdown.

A *worker* is a long-running Python process that claims jobs from PostgreSQL and runs them. Start it with `soniq worker`. Run one or more workers per host, and let your process manager (systemd, Kubernetes, supervisord) keep them alive.

Soniq workers are async. They use `asyncio` tasks, not threads. One worker process runs many jobs at the same time on one event loop.

## Starting a worker

Start a worker with the CLI:

```bash
soniq worker
soniq worker --concurrency 8 --queues emails,billing
```

**Run-once mode** processes all ready jobs and exits. Use it for tests and cron-driven runs:

```bash
soniq worker --run-once
```

Each `soniq worker` command starts one worker process. Run it from your process manager and let the manager restart and scale it. The `soniq.run_worker(...)` method exists for tests and embedded use. Do not use it in production, because you lose restart-on-crash, log capture, and shutdown timeouts.

The worker needs to know your job modules. Set `SONIQ_JOBS_MODULES`, or pass an app path such as `soniq worker myapp.jobs:soniq`. See [Job module discovery](../getting-started/installation.md#job-module-discovery).

## Concurrency model

Each worker runs a set number of asyncio tasks (default: 4). Set the number with `--concurrency`. The tasks share one event loop and use a semaphore to limit how many jobs run at once. Therefore:

- I/O-bound jobs (HTTP calls, database queries, email) scale well with higher concurrency.
- CPU-bound jobs block the event loop. Move the CPU work to a thread with `asyncio.to_thread()`:

```python
@soniq.job(name="reports.generate_pdf")
async def generate_pdf(report_id: str):
    report = await fetch_report(report_id)
    # Offload CPU work to a thread
    pdf_bytes = await asyncio.to_thread(render_pdf, report)
    await upload_pdf(report_id, pdf_bytes)
```

## Connection pool sizing

The connection pool must be large enough for your worker concurrency plus the internal connections (the LISTEN/NOTIFY listener and the heartbeat writer).

The formula: `pool_max_size >= concurrency + pool_headroom`

The defaults are `pool_max_size=20` and `pool_headroom=2`. Soniq logs a warning at startup if the pool is too small for the concurrency.

```bash
export SONIQ_POOL_MAX_SIZE=30
export SONIQ_POOL_HEADROOM=2
soniq worker --concurrency 25
```

## Job pickup: LISTEN/NOTIFY

When you enqueue a job, Soniq sends a PostgreSQL `NOTIFY` on the `soniq_new_job` channel. Workers that listen on the channel wake up at once. They compete for the job with `SELECT ... FOR UPDATE SKIP LOCKED`. One worker wins and runs the job. The other workers continue to wait.

This gives fast job pickup without constant polling.

> **Note:** LISTEN/NOTIFY is a PostgreSQL feature. The SQLite and memory backends poll at the `poll_interval` setting (default: 5 seconds).

## Heartbeat system

Workers send a heartbeat at a fixed interval. Soniq uses the heartbeats to find out which workers are alive.

| Setting | Default | Description |
| --- | --- | --- |
| `SONIQ_HEARTBEAT_INTERVAL` | `5` (seconds) | How often a worker writes a heartbeat |
| `SONIQ_HEARTBEAT_TIMEOUT` | `300` (seconds) | A worker with no heartbeat for this long is stale |

A heartbeat is a timestamp update in the `soniq_workers` table. The dashboard and the `soniq inspect` command use it to show worker status.

## Graceful shutdown

A worker handles `SIGINT` (Ctrl+C) and `SIGTERM`. When it gets either signal, it does these steps:

1. Stop accepting new jobs.
2. Wait for in-flight jobs to finish.
3. Mark the worker as stopped in the database.
4. Close the connection pool.

This is safe in Docker, Kubernetes, and systemd. Send `SIGTERM` and the worker drains its jobs and exits.

## Crash recovery

A worker that gets `SIGKILL` or dies from an out-of-memory kill cannot run its shutdown steps. Its in-flight jobs stay in `processing` status until the worker's heartbeat is older than `SONIQ_HEARTBEAT_TIMEOUT` and a cleanup runs.

To recover these jobs at once, run:

```bash
# Show stale workers
soniq inspect --stale

# Clean up stale workers and release their jobs
soniq inspect --cleanup
```

The cleanup marks stale workers as stopped. It also sets their in-flight jobs back to `queued`, so another worker runs them.

Running workers also do this cleanup on their own every 5 minutes (the `cleanup_interval` setting). One worker does the cleanup at a time. You only need `soniq inspect --cleanup` when no worker is running or when you want the recovery to happen sooner.

## Worker lifecycle hooks

Register hooks on the app to run code before every job, after every job, or on errors. Soniq passes the hook arguments by position, in the order shown:

```python
@soniq.before_job
async def log_job_start(job_name, job_id, attempt):
    logger.info(f"Starting {job_name} ({job_id})")

@soniq.after_job
async def log_job_complete(job_name, job_id, duration_ms):
    logger.info(f"Completed {job_name} ({job_id})")

@soniq.on_error
async def log_job_error(job_name, job_id, error, attempt):
    logger.error(f"Failed {job_name} ({job_id}): {error}")
```

A failing hook is logged and ignored. It never blocks the job.

## Next steps

Continue with [chapter 3: retries](03-retries.md).
