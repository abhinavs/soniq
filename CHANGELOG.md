# Changelog

All notable changes to Soniq are documented in this file.

The format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.0.2]

First public release.

### Highlights

- PostgreSQL-backed async job queue on `asyncpg`. Bundled SQLite backend for local dev and an in-memory backend for tests.
- **Transactional enqueue**: pass an existing `asyncpg` connection to `app.enqueue(...)` and the job is inserted inside your own transaction. It commits or rolls back with your data.
- **Recurring jobs** via `@app.periodic(cron="...")` or `@app.periodic(every_minutes=N)`. Requires a separate `soniq scheduler` process alongside `soniq worker`.
- **Job results**: return values from completed jobs are persisted and retrievable via `await app.get_result(job_id)`.
- **Dead-letter queue**, per-job timeouts, deduplication (`unique=True` and `dedup_key`), priorities, and multiple queues.
- **Graceful shutdown** with worker heartbeat + stale-worker sweep.
- **Bulk enqueue**: `app.enqueue_many(target, [args, ...])` writes a batch in a single round trip.
- **CLI**: `soniq setup`, `soniq worker`, `soniq scheduler`, `soniq dashboard`, `soniq status`, `soniq inspect`, dead-letter management.
- **Optional web dashboard** (`soniq dashboard`).
- **Structured logging** and **webhook delivery** behind optional extras.
- **Pluggable extension points**: `RetryPolicy` and `MetricsSink`, each with a default and a `Soniq(...)` constructor parameter. `PrometheusMetricsSink` ships in the default install and emits `soniq_jobs_started_total`, `soniq_jobs_completed_total`, `soniq_job_duration_seconds`, and `soniq_jobs_in_progress`.

### Cross-service enqueue

`app.enqueue` accepts three input shapes selected by the type of the first argument:

- **Callable** (single-repo): `app.enqueue(my_func, x=1)`.
- **String task name** (cross-service): `app.enqueue("users.task", args={"x": 1})`. The producer does not need to import the consumer's handler.
- **`TaskRef`** (typed cross-repo stub): `app.enqueue(my_ref, args={"x": 1})`. Validates `args` against the ref's `args_model` and uses its `default_queue` when no explicit `queue=` is passed.

Use `@app.job(name=...)` for stable wire-protocol identifiers. When omitted, the task name is derived from `f"{module}.{qualname}"` (matching Celery / Dramatiq / RQ). Cross-service deployments should pass `name=` explicitly; explicit names are validated against `SONIQ_TASK_NAME_PATTERN`.

`SONIQ_ENQUEUE_VALIDATION` controls how `enqueue("string-name", ...)` handles a name not registered locally. Default `"strict"` raises `SONIQ_UNKNOWN_TASK_NAME`. `"warn"` emits a rate-limited per-process warning. `"none"` proceeds silently.

Error codes: `SONIQ_UNKNOWN_TASK_NAME`, `SONIQ_INVALID_TASK_NAME`, `SONIQ_TASK_ARGS_INVALID`.

### Contracts

- `soniq.types.QueueStats` is the canonical 6-key shape returned by every backend's `queue_stats()` and surfaced in CLI / dashboard: `{total, queued, processing, done, dead_letter, cancelled}`.
- `soniq_jobs.status` is pinned to four live values: `queued / processing / done / cancelled`. Failures either re-queue or move into `soniq_dead_letter_jobs`; there is no `failed` row state.
- DLQ is a table-of-record under `soniq_dead_letter_jobs`. The runtime is the only path that creates DLQ rows. List, replay, and purge operations live on `DeadLetterService` (`replay` / `bulk_replay`); the CLI exposes them as `soniq dead-letter replay`. Replay preserves the DLQ row as the audit trail, increments `resurrection_count`, and enqueues a fresh `soniq_jobs` row.
- Bounded sync handler thread pool: `sync_handler_pool_size` (default `8`) caps concurrent sync handler threads per `Soniq` instance. Async handlers bypass the pool.
- `shutdown_timeout` (default `30s`) and `sync_handler_grace_seconds` drive the `RUNNING -> DRAINING -> FORCE_TIMEOUT_PATH` shutdown state machine. Async jobs nack on force-timeout; sync jobs receive an extra grace window.
- Two-instance isolation is a tested contract: per-instance settings, registries, and backends, pinned by the `check_no_global_settings.py` pre-commit hook and a cross-instance bleed integration test.

### Operational notes

- `SONIQ_DATABASE_URL` is the primary configuration input. Every other setting (`SONIQ_CONCURRENCY`, `SONIQ_POOL_MAX_SIZE`, feature flags) has a sensible default.
- `soniq setup` applies the baseline schema. It is idempotent. All tables are namespaced `soniq_*` and are created unconditionally; tables for unused features stay empty but present.
- LISTEN/NOTIFY channel is `soniq_new_job`. Advisory-lock namespaces are `soniq.maintenance` (worker cleanup) and `soniq.migrations` (migration runner).
- Default SQLite backend filename is `soniq.db`.
- Connection pool sizing is validated at worker startup: `SONIQ_POOL_MAX_SIZE` must be at least `SONIQ_CONCURRENCY + SONIQ_POOL_HEADROOM`; the worker refuses to start otherwise.
- Packaging is batteries-included: `croniter` and `prometheus_client` ship in the default install, so `@periodic` and `PrometheusMetricsSink` work from a plain `pip install soniq`. The `dashboard`, `sqlite`, `webhooks`, and `logging` extras remain opt-in.

### Recurring jobs need a scheduler sidecar

`soniq worker` runs the worker only. If you use `@app.periodic(...)` jobs, deploy a separate `soniq scheduler` process. The worker prints a one-time WARN at startup if it detects `@periodic` decorators and no scheduler is configured. Suppress with `SONIQ_SCHEDULER_SUPPRESS_WARNING=1`.

The shipped deployment templates (systemd, Docker Compose, Kubernetes, Supervisor) all include the sidecar; see `docs/production/deployment.md`.

### Known limitations

- Sync handler hard-kill on shutdown can re-deliver a job whose handler was mid-flight when the executor was forced down. There is no exactly-once guarantee for sync handlers under `shutdown_timeout`-triggered force-paths; design handlers to be idempotent.
