# Going to production

The 80/20 of running Soniq in production. If you do the four things in
**Required** below and the four things in **Strongly recommended**,
you have a healthy deploy. Everything else on this section is detail.

## At a glance

A copy-into-the-ticket checklist:

- [ ] `SONIQ_DATABASE_URL` and `SONIQ_JOBS_MODULES` set in the worker environment
- [ ] `soniq setup` runs once per deploy (CI step, init container, or migration job - never from app startup on every replica)
- [ ] Process manager sends `SIGTERM` for shutdown, with `terminationGracePeriodSeconds` / `TimeoutStopSec` >= longest job timeout
- [ ] Handlers are idempotent (safe to run more than once - upserts, dedup keys, idempotency tokens)
- [ ] A metrics sink wired up (Prometheus or your own `MetricsSink`)
- [ ] If you use `@app.periodic`, a separate `soniq scheduler` process is running
- [ ] `SONIQ_POOL_MAX_SIZE >= concurrency + SONIQ_POOL_HEADROOM`, and `max_connections` on the database has headroom for `num_workers * pool_size`
- [ ] Per-job timeouts set for slow jobs; the global default is 300s

The rest of this page explains each item. The [common production mistakes](#common-production-mistakes) section at the bottom is the most useful page on the site if something is misbehaving in production - read it first if you are debugging.

## Required

### Set `SONIQ_DATABASE_URL` and `SONIQ_JOBS_MODULES`

| Variable | Example | Why |
|---|---|---|
| `SONIQ_DATABASE_URL` | `postgresql://user:pass@host/db` | Postgres connection string. |
| `SONIQ_JOBS_MODULES` | `myapp.jobs,myapp.billing` | Modules the worker imports on startup so `@app.job` decorators run. Workers cannot process jobs they cannot import. |

### Run `soniq setup` only once per deploy

`soniq setup` applies versioned migrations and is idempotent, but multiple replicas racing to apply the same migrations cause confusing errors. Run it from a CI step, a Kubernetes init container, a dedicated migration job, or an entrypoint that fires only on the first replica. Never from application startup on every replica.

### Stop workers with `SIGTERM`, not `SIGKILL`

Soniq handles `SIGTERM` by finishing in-flight jobs before exiting. `SIGKILL` (or OOM) leaves jobs stuck in `processing` until the heartbeat sweep recovers them, which takes up to `SONIQ_HEARTBEAT_TIMEOUT` (default 300s). Match the grace window in your process manager:

- **systemd:** `TimeoutStopSec=<longest job timeout + buffer>`
- **Kubernetes:** `terminationGracePeriodSeconds: <same>`
- **Supervisor:** `stopwaitsecs=<same>`

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
from soniq.metrics import PrometheusMetricsSink

app = Soniq(metrics_sink=PrometheusMetricsSink())
```

Mount `/metrics` from `prometheus_client.make_asgi_app()` on whatever HTTP surface you scrape, or run `soniq dashboard` and point Grafana at its `/api/metrics`.

### Run `soniq scheduler` if you use `@app.periodic`

The CLI worker (`soniq worker`) does **not** evaluate due recurring jobs. Run a separate `soniq scheduler` process. Multiple instances coordinate via a Postgres advisory lock; runners-up wait for the leader.

### Pool sizing

Each worker maintains its own asyncpg pool. Soniq reserves `SONIQ_POOL_HEADROOM` connections (default 2) for the LISTEN/NOTIFY listener and heartbeat writer:

```
SONIQ_POOL_MAX_SIZE >= concurrency + SONIQ_POOL_HEADROOM
```

Total Postgres load is `num_worker_processes * SONIQ_POOL_MAX_SIZE`. Confirm `max_connections` on the database can handle that plus your application's pools and admin sessions.

### Per-job timeouts

The global default is `SONIQ_JOB_TIMEOUT=300` seconds. Override for slow jobs with `@app.job(timeout=600)`; set to `0` to disable per-job. Without a timeout, a hung handler ties up a concurrency slot until the worker restarts.

## Common production mistakes

- **Running `soniq setup` from application startup on every replica.** Replicas race, migrations error out. Run it in one place per deploy.
- **`concurrency` higher than the pool can serve.** Raises `Connection pool too small`. Either raise `SONIQ_POOL_MAX_SIZE` or lower concurrency.
- **Sync handlers monopolising the bounded thread pool.** `def` (non-async) handlers run on a pool of `SONIQ_SYNC_HANDLER_POOL_SIZE` (default 8). If those are slow, async handlers cannot claim slots. Convert to `async def` or raise the pool size.
- **Worker host does not have your job code installed.** `SONIQ_JOBS_MODULES=myapp.jobs` is an instruction to *import* `myapp.jobs`, not a way to ship code. Bake the code into the worker image.
- **PgBouncer in transaction-pooling mode.** Breaks `LISTEN/NOTIFY` (workers fall back to polling) and breaks the scheduler advisory lock. Use session pooling for worker and scheduler connections.
- **`SONIQ_DATABASE_URL` divergence between `setup` and `worker`.** `setup` writes to URL A; the worker reads URL B; the worker logs "table does not exist."
- **Forgetting `await` after switching from Celery.** Celery's `.delay()` is synchronous. Soniq's `enqueue(...)` is a coroutine. A bare `app.enqueue(send_email, ...)` schedules nothing. Treat the `RuntimeWarning: coroutine was never awaited` warning as an error in CI.
- **Resurrecting a full DLQ without fixing the cause.** `soniq dead-letter replay --all` re-runs every dead-letter job. Use `--dry-run` first, fix the underlying bug, then replay.

## Next steps

- [Deployment recipes](deployment.md) - systemd, Docker Compose, Kubernetes, Supervisor
- [PostgreSQL tuning](postgres.md) - pool sizing, connection ceilings, fsync notes
- [Reliability](reliability.md) - what at-least-once means, recovering from crashes
- [Troubleshooting](../troubleshooting.md) - symptom -> cause -> fix
