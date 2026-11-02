---
description: Production checklist for Soniq, the PostgreSQL job queue for Python - setup, graceful shutdown, pool sizing, idempotent jobs, scheduler and common mistakes.
---

# Production checklist for Soniq workers

This page lists what a healthy Soniq deploy on PostgreSQL needs. Do the items under **Required** and **Strongly recommended**. The other pages in this section give the detail.

## At a glance

A copy-into-the-ticket checklist:

- [ ] `SONIQ_DATABASE_URL` and `SONIQ_JOBS_MODULES` set in the worker environment
- [ ] `soniq setup` runs once per deploy (CI step, init container, or migration job - never from app startup on every replica)
- [ ] Process manager sends `SIGTERM` for shutdown, with `terminationGracePeriodSeconds` / `TimeoutStopSec` >= longest job timeout, and `SONIQ_SHUTDOWN_TIMEOUT` set for your async jobs
- [ ] Handlers are idempotent (safe to run more than once - upserts, dedup keys, idempotency tokens)
- [ ] A metrics sink wired up (Prometheus or your own `MetricsSink`)
- [ ] If you use `@soniq.periodic`, a separate `soniq scheduler` process is running
- [ ] `SONIQ_POOL_MAX_SIZE >= concurrency + SONIQ_POOL_HEADROOM`, and `max_connections` on the database has headroom for `num_workers * pool_size`
- [ ] Per-job timeouts set for slow jobs; the global default is 300s
- [ ] On a database created before Postgres 15 (`pg_upgrade` keeps the old default), with more than one role in the database: `REVOKE CREATE ON SCHEMA public FROM PUBLIC`

The rest of this page explains each item. If something misbehaves in production, read [common production mistakes](#common-production-mistakes) first.

## Required

### Set `SONIQ_DATABASE_URL` and `SONIQ_JOBS_MODULES`

| Variable | Example | Why |
|---|---|---|
| `SONIQ_DATABASE_URL` | `postgresql://user:pass@host/db` | Postgres connection string. |
| `SONIQ_JOBS_MODULES` | `myapp.jobs,myapp.billing` | Modules the worker imports on startup so the `@soniq.job()` decorators run. Workers cannot process jobs they cannot import. |

### Run `soniq setup` only once per deploy

`soniq setup` applies versioned migrations and is idempotent. Do not run it from every replica at startup, because replicas that race to apply the same migrations can cause confusing errors. Run it from one place: a CI step, a Kubernetes `Job` or init container, a dedicated migration job, or an entrypoint that runs on the first replica only.

### Stop workers with `SIGTERM`, not `SIGKILL`

On `SIGTERM`, the worker stops claiming jobs and waits for in-flight jobs. It waits up to `SONIQ_SHUTDOWN_TIMEOUT` seconds (default 30). After that, it cancels in-flight async jobs and returns them to `queued` without counting an attempt. For a sync handler, the worker waits a further `SONIQ_SYNC_HANDLER_GRACE_SECONDS` (default: the job timeout) and then keeps waiting for the thread. To let async jobs finish, raise `SONIQ_SHUTDOWN_TIMEOUT` to your longest job runtime.

`SIGKILL` (or an out-of-memory kill) leaves jobs stuck in `processing`. The heartbeat sweep recovers them after up to `SONIQ_HEARTBEAT_TIMEOUT` (default 300s). Set the grace window in your process manager to at least `SONIQ_SHUTDOWN_TIMEOUT`, and to the longest sync job timeout if you use sync handlers:

- **systemd:** `TimeoutStopSec=<longest job timeout + buffer>`
- **Kubernetes:** `terminationGracePeriodSeconds: <same>`
- **Supervisor:** `stopwaitsecs=<same>`

### On a database created before Postgres 15, revoke `CREATE` on `public`

If any role other than Soniq's own can log in to the database, run this once as the database owner:

```sql
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
```

Before Postgres 15 every role can create functions in `public`, and a function there can shadow a built-in that Postgres, Soniq or your own code calls, so it runs as whoever calls it, the superuser that runs `soniq setup` included. New databases on Postgres 15 and newer already ship with this revoked, but a cluster brought up from 14 or older with `pg_upgrade` keeps the old grant. To check, `SELECT has_schema_privilege('public', 'public', 'CREATE')` returns `true` when you still need the `REVOKE`.

### Design jobs to be idempotent

Soniq guarantees *at-least-once* delivery: a job will run at least once, and may run more than once. If a worker crashes after running your handler but before marking the row `done`, the heartbeat sweep will requeue the job and another worker will run it. *Idempotent* means "safe to run more than once with the same end result" - use upserts (`INSERT ... ON CONFLICT DO UPDATE`), dedup checks against current state, or idempotency keys for any side effect you do not want to repeat. The fix is "make the second run a no-op", not "guarantee the second run never happens" - on Postgres alone the latter is not possible.

## Strongly recommended

### Logging and observability

```bash
export SONIQ_LOG_LEVEL=INFO
```

Wire a metrics sink:

```python
from soniq import Soniq
from soniq.observability import PrometheusMetricsSink

soniq = Soniq(metrics_sink=PrometheusMetricsSink())
```

Mount `/metrics` from `prometheus_client.make_asgi_app()` on the HTTP surface you scrape. Or run `soniq dashboard` and read its JSON `/api/metrics` endpoint.

### Run `soniq scheduler` if you use `@soniq.periodic`

The CLI worker (`soniq worker`) does **not** evaluate due recurring jobs. Run a separate `soniq scheduler` process. Multiple instances coordinate through a Postgres advisory lock, and the runners-up wait for the leader.

### Pool sizing

Each worker maintains its own asyncpg pool. Soniq reserves `SONIQ_POOL_HEADROOM` connections (default 2) for the LISTEN/NOTIFY listener and heartbeat writer:

```
SONIQ_POOL_MAX_SIZE >= concurrency + SONIQ_POOL_HEADROOM
```

Total Postgres load is `num_worker_processes * SONIQ_POOL_MAX_SIZE`. Confirm `max_connections` on the database can handle that plus your application's pools and admin sessions.

### Per-job timeouts

The global default is `SONIQ_JOB_TIMEOUT=300` seconds. Override it for slow jobs with `@soniq.job(name="reports.build", timeout=600)`. Set `timeout=0` to disable the timeout for one job. Without a timeout, a hung handler holds a concurrency slot until the worker restarts.

## Common production mistakes

- **Running `soniq setup` from application startup on every replica.** Replicas race, migrations error out. Run it in one place per deploy.
- **`concurrency` higher than the pool can serve.** Raises `Connection pool too small`. Either raise `SONIQ_POOL_MAX_SIZE` or lower concurrency.
- **Sync handlers monopolising the bounded thread pool.** `def` (non-async) handlers run on a pool of `SONIQ_SYNC_HANDLER_POOL_SIZE` (default 8). If those are slow, async handlers cannot claim slots. Convert to `async def` or raise the pool size.
- **Worker host does not have your job code installed.** `SONIQ_JOBS_MODULES=myapp.jobs` is an instruction to *import* `myapp.jobs`, not a way to ship code. Bake the code into the worker image.
- **PgBouncer in transaction-pooling mode.** Breaks `LISTEN/NOTIFY` (workers fall back to polling) and breaks the scheduler advisory lock. Use session pooling for worker and scheduler connections.
- **`SONIQ_DATABASE_URL` divergence between `setup` and `worker`.** `setup` writes to URL A; the worker reads URL B; the worker logs "table does not exist."
- **Forgetting `await` after switching from Celery.** Celery's `.delay()` is synchronous. Soniq's `enqueue(...)` is a coroutine. A bare `soniq.enqueue(send_email, ...)` schedules nothing. Treat the `RuntimeWarning: coroutine was never awaited` warning as an error in CI.
- **Resurrecting a full DLQ without fixing the cause.** `soniq dead-letter replay --all` re-runs every dead-letter job. Use `--dry-run` first, fix the underlying bug, then replay.

## Next steps

- [Deployment recipes](deployment.md) - systemd, Docker Compose, Kubernetes, Supervisor
- [PostgreSQL tuning](postgres.md) - pool sizing, connection ceilings, fsync notes
- [Reliability](reliability.md) - what at-least-once means, recovering from crashes
- [Troubleshooting](../troubleshooting.md) - symptom -> cause -> fix
