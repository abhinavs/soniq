---
description: What Soniq guarantees on PostgreSQL - at-least-once delivery, idempotent job patterns, retries, stuck job recovery after a crash, and database failure behavior.
---

# Delivery guarantees and failure recovery

Soniq delivers each job at least once, not exactly once. This page explains what that means, how to write idempotent jobs, and how Soniq recovers from worker and database failures.

## Delivery guarantees

Soniq provides **at-least-once delivery**. Every enqueued job will be executed at least once, assuming workers are running and the database is available.

It does not provide exactly-once delivery. Here's why: there is a window between when a job finishes executing and when its status is updated in the database. If the worker crashes in that window (SIGKILL, OOM, kernel panic, pod eviction), the job stays in `processing` status. When a healthy worker eventually detects the stale worker and resets the job, it runs again.

This is a fundamental property of any job queue that doesn't require two-phase commit between your job logic and the queue's state store. It's not a bug.

## Idempotency

Because jobs can run more than once, write every job to be idempotent. An idempotent job gives the same result when it runs twice as when it runs once.

Common patterns:

**Upserts instead of inserts.** Use `INSERT ... ON CONFLICT DO UPDATE` so a duplicate run overwrites rather than creating a second row.

**Sent flags.** Before sending an email or webhook, check a flag in your database. Set the flag inside the same transaction as the action.

```python
@soniq.job(name="emails.send_welcome_email")
async def send_welcome_email(user_id: int):
    user = await db.get(user_id)
    if user.welcome_email_sent:
        return  # already done
    await send_email(user.email, "Welcome!")
    await db.update(user_id, welcome_email_sent=True)
```

**Idempotency keys.** For external API calls (payment providers, etc.), pass a deterministic key derived from the job ID or payload. The external service deduplicates on its end.

**Deterministic outputs.** If a job writes a file, use a deterministic filename based on the input. Re-running overwrites rather than creating duplicates.

## Retry semantics

When a job raises an exception, Soniq retries it automatically up to `retries` times (default 3, from `SONIQ_MAX_RETRIES`). The first run plus 3 retries gives 4 attempts. Each retry waits for the configured delay.

```python
@soniq.job(name="jobs.call_external_api", retries=5, timeout=120)
async def call_external_api(payload: dict):
    response = await httpx.post("https://api.example.com", json=payload)
    response.raise_for_status()
```

After exhausting retries the job is moved into the dead-letter table.
Inspect and replay with `soniq dead-letter list` and `soniq dead-letter
replay <id>`.

## Stuck job recovery

### What causes stuck jobs

A job gets stuck in `processing` status when the worker running it dies without updating the database. Common causes:

- `SIGKILL` (e.g., `kill -9`, systemd `TimeoutStopSec` exceeded)
- OOM killer
- Kubernetes pod eviction
- Host crash or power loss
- Network partition between worker and database lasting longer than the job execution

### Automatic recovery

One running worker at a time scans for stale peers every `SONIQ_CLEANUP_INTERVAL` seconds. When a worker's last heartbeat is older than `SONIQ_HEARTBEAT_TIMEOUT` (default 300 seconds), the scan marks the worker `stopped` and resets its in-flight jobs to `queued`. Healthy workers then pick them up.

Worst-case recovery time is `SONIQ_HEARTBEAT_TIMEOUT` plus `SONIQ_CLEANUP_INTERVAL`. With the defaults (300 and 300), that is 10 minutes. The minimum for `SONIQ_HEARTBEAT_TIMEOUT` is 60 and the minimum for `SONIQ_CLEANUP_INTERVAL` is 10. To recover faster, lower both:

```bash
SONIQ_HEARTBEAT_TIMEOUT=120   # 2 minutes
SONIQ_CLEANUP_INTERVAL=60     # check every minute
```

The default 300-second job timeout also stops most stuck jobs caused by hung code, such as infinite loops and dead network calls. Override it for one job with `@soniq.job(name="reports.build", timeout=600)`.

### Manual recovery

If no workers are running, or you need to recover faster, reset stuck jobs directly. **Warning:** this statement requeues every job it matches, so a job that is still running can run twice. Set the interval longer than your longest-running job. If a job legitimately runs for 30 minutes, use `INTERVAL '35 minutes'`.

```sql
UPDATE soniq_jobs
SET status = 'queued', worker_id = NULL, updated_at = NOW()
WHERE status = 'processing'
  AND updated_at < NOW() - INTERVAL '10 minutes';
```

### Cleaning up stale workers

```bash
soniq inspect --cleanup
```

The command also requeues the jobs of stale workers. The manual SQL below only marks the worker records `stopped`:

```sql
UPDATE soniq_workers
SET status = 'stopped'
WHERE status = 'active'
  AND last_heartbeat < NOW() - INTERVAL '10 minutes';
```

### Detecting stuck jobs

```sql
SELECT id, job_name, queue, attempts, updated_at
FROM soniq_jobs
WHERE status = 'processing'
  AND updated_at < NOW() - INTERVAL '10 minutes'
ORDER BY updated_at ASC;
```

Check for stale workers:

```bash
soniq inspect --stale
```

## Worker crash behavior

Workers send heartbeats every `SONIQ_HEARTBEAT_INTERVAL` seconds (default 5). If a worker stops heartbeating, it's considered stale after `SONIQ_HEARTBEAT_TIMEOUT` seconds (default 300).

When a worker is detected as stale:

1. Its in-flight jobs are reset to `queued`.
2. Its worker record is marked `stopped`.
3. The jobs are picked up by healthy workers.

Preventive measures:

- Use `SIGTERM` for graceful shutdown. The worker waits up to `SONIQ_SHUTDOWN_TIMEOUT` seconds (default 30) for in-flight jobs, then returns unfinished async jobs to `queued`.
- In Kubernetes, set `terminationGracePeriodSeconds` to at least your longest job timeout.
- In systemd, set `TimeoutStopSec` to at least your longest job timeout.
- Set a memory limit in your process manager, so a leaking handler restarts one worker and not the host.

## Database failure behavior

If the database becomes unreachable:

- Workers cannot pick up new jobs. They enter a reconnect loop with exponential backoff starting at `SONIQ_ERROR_RETRY_DELAY` seconds (default 5).
- In-flight jobs that are purely in-memory (no DB calls) may continue running. When they finish, the status update will fail. The job will be retried after the worker reconnects and the stale detection kicks in.
- LISTEN/NOTIFY stops working. After reconnection, workers fall back to polling temporarily, then re-establish listeners.

Recovery steps:

1. Fix the database.
2. Workers reconnect automatically. No manual intervention needed in most cases.
3. If workers crashed during the outage, start new workers. They'll pick up any stuck jobs from the dead workers.

## Things that surprise people

These aren't bugs. They're design decisions and tradeoffs.

### Retries mean jobs run more than once

At-least-once delivery is the whole point. If a worker crashes after your job's side effects but before the status update, the job runs again. Design for idempotency.

### SQLite is for development only

SQLite works for prototyping and local development, but it has hard limitations:

- Single worker process only (no concurrent writers)
- No `LISTEN/NOTIFY`: workers poll for new jobs
- No `FOR UPDATE SKIP LOCKED`: no safe concurrent dequeue
- No transactional enqueue

Do not run SQLite in production.

### PostgreSQL scaling limits

Soniq's [benchmark page](../benchmarks.md) measures 291 to 1682 jobs per second on a single PostgreSQL instance, depending on queue depth. Throughput falls as the backlog of waiting jobs grows. If you hit the limit of one instance, consider:

- Partitioning the jobs table by queue
- Dedicated PostgreSQL instances per queue group
- Reducing payload sizes
- Archiving completed jobs aggressively (`SONIQ_RESULT_TTL`)

### Stuck jobs after SIGKILL or OOM

When a worker is killed without a chance to clean up, its in-flight jobs stay in `processing` until another worker detects the stale heartbeat. With defaults, that's up to 10 minutes. This is expected behavior, not data loss. Soniq retries the jobs.

### Connection pool sizing matters

If your pool is too small, workers block waiting for connections and throughput drops. If it's too large, you exhaust PostgreSQL `max_connections`. The formula is straightforward:

```
pool_max_size >= concurrency + headroom
```

See [PostgreSQL tuning](postgres.md) for details.
