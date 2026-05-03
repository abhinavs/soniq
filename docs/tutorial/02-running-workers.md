# 2. Running workers

> **Beginner** - 8 minutes. Starting workers, concurrency, graceful shutdown.

A *worker* is a long-running Python process that fetches jobs from the database and runs them. You typically run one or more workers per host, and your process manager (systemd, Kubernetes, supervisord) keeps them alive.

Soniq workers are async - they use `asyncio` tasks, not threads. One worker process can handle many concurrent jobs at once on a single event loop.

## Starting a worker

The primary way to run a worker is the CLI:

```bash
soniq worker
soniq worker --concurrency 8 --queues emails,billing
```

**Run-once mode** processes all available jobs and exits. Useful for testing and cron-style batch processing:

```bash
soniq worker --run-once
```

Each invocation supervises one worker process. Run it from your process manager (systemd, Kubernetes Deployment, supervisord) and let the manager handle restarts and scaling. The programmatic `app.run_worker(...)` entry point exists for tests and embedded scenarios; do not use it for production deployments -- you lose the process-manager safety net (restart-on-crash, log capture, graceful-shutdown timeouts).

## Concurrency model

Each worker runs a configurable number of asyncio tasks (default: 4). These tasks share a single event loop and compete for jobs using a semaphore. This means:

- I/O-bound jobs (HTTP calls, database queries, email sending) scale well with higher concurrency.
- CPU-bound jobs block the event loop. Wrap them in `asyncio.to_thread()`:

```python
@app.job()
async def generate_pdf(report_id: str):
    report = await fetch_report(report_id)
    # Offload CPU work to a thread
    pdf_bytes = await asyncio.to_thread(render_pdf, report)
    await upload_pdf(report_id, pdf_bytes)
```

## Connection pool sizing

The connection pool should be large enough to handle your worker concurrency plus headroom for internal connections (LISTEN/NOTIFY listener and heartbeat writer).

The formula: `pool_max_size >= concurrency + pool_headroom`

Defaults: `pool_max_size=20`, `pool_headroom=2`. Soniq warns at startup if your pool is too small for the configured concurrency.

```bash
export SONIQ_POOL_MAX_SIZE=30
export SONIQ_POOL_HEADROOM=2
soniq worker --concurrency 25
```

## Job pickup: LISTEN/NOTIFY

When a job is enqueued, Soniq sends a PostgreSQL `NOTIFY` on the `soniq_new_job` channel. Workers listening on that channel wake up immediately and compete for the job using `SELECT ... FOR UPDATE SKIP LOCKED`. The winner processes it; the losers move on.

This makes job pickup near-instant (typically under 10ms) without polling overhead.

> **Note:** LISTEN/NOTIFY is a PostgreSQL feature. SQLite and memory backends fall back to polling at the configured `poll_interval` (default: 5 seconds).

## Heartbeat system

Workers send periodic heartbeats so Soniq can detect which workers are alive.

| Setting | Default | Description |
| --- | --- | --- |
| `SONIQ_HEARTBEAT_INTERVAL` | `5s` | How often a worker writes a heartbeat |
| `SONIQ_HEARTBEAT_TIMEOUT` | `300s` | After this long without a heartbeat, a worker is considered stale |

The heartbeat is a simple timestamp update in the `soniq_workers` table. The dashboard and `soniq inspect` CLI command use it to show live worker status.

## Graceful shutdown

Workers handle `SIGINT` (Ctrl+C) and `SIGTERM`:

1. Stop accepting new jobs.
2. Wait for in-flight jobs to finish.
3. Mark the worker as stopped in the database.
4. Close the connection pool.

This makes Soniq safe in Docker, Kubernetes, and systemd environments. Send `SIGTERM` and the worker drains gracefully.

## Crash recovery

If a worker is killed with `SIGKILL` or dies from an OOM, it cannot run its shutdown sequence. In-flight jobs are left in `processing` status and will not be picked up by other workers automatically.

To recover these jobs:

```bash
# Show stale workers
soniq inspect --stale

# Clean up stale workers and release their jobs
soniq inspect --cleanup
```

The cleanup operation marks stale workers as stopped and resets their in-flight jobs back to `queued` status so they can be retried.

> **Warning:** In production, run `soniq inspect --cleanup` on a schedule (e.g., via cron) or rely on the periodic cleanup that running workers perform automatically every 5 minutes.

## Worker lifecycle hooks

Register hooks on the app to run code before/after every job or on errors:

```python
@app.before_job
async def log_job_start(job_name, job_id, args):
    logger.info(f"Starting {job_name} ({job_id})")

@app.after_job
async def log_job_complete(job_name, job_id, result):
    logger.info(f"Completed {job_name} ({job_id})")

@app.on_error
async def log_job_error(job_name, job_id, error):
    logger.error(f"Failed {job_name} ({job_id}): {error}")
```
