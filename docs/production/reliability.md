# Reliability

This document covers what Soniq guarantees, what it doesn't, and how to handle the failure modes you'll encounter in production.

## Delivery guarantees

Soniq provides **at-least-once delivery**. Every enqueued job will be executed at least once, assuming workers are running and the database is available.

It does not provide exactly-once delivery. Here's why: there is a window between when a job finishes executing and when its status is updated in the database. If the worker crashes in that window (SIGKILL, OOM, kernel panic, pod eviction), the job stays in `processing` status. When a healthy worker eventually detects the stale worker and resets the job, it runs again.

This is a fundamental property of any job queue that doesn't require two-phase commit between your job logic and the queue's state store. It's not a bug.

## Idempotency

Because jobs can run more than once, all jobs should be idempotent. Running a job twice should produce the same result as running it once.

Common patterns:

**Upserts instead of inserts.** Use `INSERT ... ON CONFLICT DO UPDATE` so a duplicate run overwrites rather than creating a second row.

**Sent flags.** Before sending an email or webhook, check a flag in your database. Set the flag inside the same transaction as the action.

```python
@app.job()
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

When a job raises an exception, Soniq retries it automatically up to `max_retries` (default 3). Each retry respects the configured delay.

```python
@app.job(max_retries=5, timeout=120)
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

Running workers periodically scan for stale peers. When a worker's heartbeat exceeds the heartbeat timeout (default 300 seconds), its in-flight jobs are reset to `queued` and picked up by healthy workers.

Worst-case recovery time = `heartbeat_timeout` + `cleanup_interval`. With defaults, that's 10 minutes. Tune for your needs:

```bash
SONIQ_HEARTBEAT_TIMEOUT=120   # 2 minutes
SONIQ_CLEANUP_INTERVAL=60     # check every minute
```

The default 300-second job timeout also prevents most stuck-job scenarios caused by hung code (infinite loops, dead network calls). Override per-job with `@app.job(timeout=600)`.

### Manual recovery

If no workers are running (or you need to recover faster), reset stuck jobs directly:

```sql
UPDATE soniq_jobs
SET status = 'queued', worker_id = NULL, updated_at = NOW()
WHERE status = 'processing'
  AND updated_at < NOW() - INTERVAL '10 minutes';
```

> **Warning:** Set the interval longer than your longest-running job. If you have jobs that legitimately run for 30 minutes, use `INTERVAL '35 minutes'`.

### Cleaning up stale workers

```bash
soniq inspect --cleanup
```

Or manually:

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

- Use `SIGTERM` for graceful shutdown. Soniq finishes in-flight jobs before exiting.
- In Kubernetes, set `terminationGracePeriodSeconds` to match your longest job timeout.
- In systemd, set `TimeoutStopSec` appropriately.
- Monitor memory usage. The `SONIQ_MEMORY_USAGE_THRESHOLD` setting (default 90%) triggers a health warning.

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
- No `LISTEN/NOTIFY` -- workers poll for new jobs
- No `FOR UPDATE SKIP LOCKED` -- no safe concurrent dequeue
- No transactional enqueue

Do not run SQLite in production.

### PostgreSQL scaling limits

Soniq is comfortable processing thousands of jobs per second on a single PostgreSQL instance. At 10,000+ jobs/sec, you'll start hitting contention on the jobs table. At that scale, consider:

- Partitioning the jobs table by queue
- Dedicated PostgreSQL instances per queue group
- Reducing payload sizes
- Archiving completed jobs aggressively (`SONIQ_RESULT_TTL`)

### Stuck jobs after SIGKILL or OOM

When a worker is killed without a chance to clean up, its in-flight jobs stay in `processing` until another worker detects the stale heartbeat. With defaults, that's up to 10 minutes. This is expected behavior, not data loss -- the jobs will be retried.

### Connection pool sizing matters

If your pool is too small, workers block waiting for connections and throughput drops. If it's too large, you exhaust PostgreSQL `max_connections`. The formula is straightforward:

```
pool_max_size >= concurrency + headroom
```

See [PostgreSQL tuning](postgres.md) for details.
