# Changelog

All notable changes to Soniq are documented in this file.

The format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - Unreleased

The first step towards rows that any language can read and write. See the new [Storage format](docs/reference/storage-format.md) page.

### Upgrading from 0.1.0

No schema change is needed. Rows written by 0.1.0 are still processed, and 0.1.0 workers can run rows written by 0.2.0, so a rolling deploy or a rollback is safe. Three behavior changes may need a look:

- A handler that returns something that isn't JSON (a `datetime`, a Pydantic model) used to have its `str()` stored as the result. Now the job still completes, the worker logs an error, and the stored result is `None`.
- `enqueue` rejects `NaN`, `Infinity` and `-Infinity` with `SONIQ_TASK_ARGS_INVALID`. They used to get past the check and fail inside the database driver.
- `@soniq.job()` without `name=` warns. See Deprecations.

### Deprecations

- **Job names derived from the module path.** `@soniq.job()` with no `name=` still registers as `module.function`, but warns and goes away in 1.0. Set `name=` to the name the warning prints, so jobs already queued under it still find their handler.

### Changed

- Soniq writes plain JSON only. The encoders no longer turn unknown objects into strings, and `NaN` is rejected instead of written.
- The docs and examples pass `name=` to every job.

### Fixed

- A `ctx: JobContext` parameter now works in modules that use `from __future__ import annotations`. Before, the annotation was a string, so the worker never passed the context and `enqueue` rejected the job with "missing a required argument: 'ctx'".
- Dead-letter `tags` passed to the Postgres backend were encoded twice and stored as a JSON string instead of an object.
- Dashboard metrics counted queued and cancelled jobs as processed and left dead-lettered jobs out. Now only finished jobs count, and failures are the jobs that reached the dead-letter table. Job types use the same rule.
- The dashboard chart counted completions in the hour a job was enqueued and dropped hours with only failures. It now buckets each event by when it happened, in whole UTC hours.
- The dashboard works when mounted under a path, sends the API key it was given, and escapes job names and errors it shows.
- The testing guide's dead-letter example used the memory fixture and `max_retries=`. It uses `soniq_pg` and `retries=`.

### Added

- **Storage format page** with the tables, the JSON rules and the exact `args_hash` algorithm, including a test vector.
- **A new dashboard.** Overview, jobs, failed jobs, queues, job types and workers, with light and dark themes, live refresh and a phone layout. Replay, cancel and delete still need write mode.
- Dashboard endpoints `GET /api/dead-letter` and `GET /api/workers`.

## [0.1.0] - 2026-10-24

### Upgrading from 0.0.5

No schema change is needed. `soniq setup` has nothing to apply. All 0.0.5 APIs, CLI commands, flags and env vars still exist. Three behavior changes may need a look:

- Dashboard writes now always need `SONIQ_DASHBOARD_API_KEY`. Localhost callers used to be exempt.
- `enqueue` rejects kwargs that don't match the handler, and args that aren't JSON. These used to be accepted and failed (or arrived as strings) in the worker.
- `get_job` returns dead-lettered jobs with `status` `"dead_letter"` instead of `None`.

### Deprecations

None in this release.

### Added

- **`enqueue(connection=...)` accepts an SQLAlchemy `AsyncSession` or `AsyncConnection`.** The job is written in the same transaction as your ORM writes. If SQLAlchemy rolls back, the job is gone too. Works with the `asyncpg` and `psycopg` drivers. Install with `pip install "soniq[sqlalchemy]"`.
- **FastAPI lifespan helper.** `from soniq.integrations.fastapi import lifespan`, then `FastAPI(lifespan=lifespan(soniq, run_worker=True))`. It initializes Soniq, can run a worker in the same process, stops it gracefully on shutdown, and closes Soniq. `extra=` chains your own lifespan.
- **`soniq worker myapp.jobs:soniq`.** `worker`, `setup`, `scheduler`, `status`, `inspect`, `dashboard` and `migrate-status` accept an optional `module:attribute` argument that points at your `Soniq` instance. All of them and `dead-letter` accept `--app module:attribute`. With an app path, the database URL comes from `--database-url`, then `SONIQ_DATABASE_URL`, then the instance. Without one, nothing changes.
- **`.enqueue()` and `.options()` on job functions.** `await send_welcome.enqueue(to="dev@example.com")` does the same as `await soniq.enqueue(send_welcome, to="dev@example.com")`, and a type checker checks the arguments against the job signature. `send_welcome.options(queue="emails", priority=1).enqueue(...)` sets enqueue options. The decorator still returns the same plain function. `soniq.enqueue(fn, ...)` stays supported and is not deprecated.
- **`SoniqError` is exported from the top-level package.** `from soniq import SoniqError` works, so you can catch `SONIQ_TASK_ARGS_INVALID` without reaching into `soniq.errors`.
- **`soniq.types.JobFunction` and `soniq.types.JobOptions`** protocols.
- **psycopg 3 for transactional enqueue.** `enqueue(connection=...)` accepts a `psycopg.AsyncConnection`. New `psycopg` extra. `soniq.integrations.sqlalchemy.to_driver_connection` returns either driver connection; `to_asyncpg_connection` stays as an alias.
- **`Soniq.run_worker(..., shutdown_event=None)`.** Pass an `asyncio.Event` to control shutdown yourself. Setting it drains in-flight jobs. When given, the worker installs no signal handlers.
- **Opt-in pytest plugin** `soniq.testing.pytest_plugin` with the `soniq_memory` and `soniq_pg` fixtures and the `soniq(app="module:attr")` marker. Enable it with `pytest_plugins = ["soniq.testing.pytest_plugin"]`. There is no `pytest11` entry point, so nothing loads unless you ask.
- **`soniq.testing.drain(soniq, max_jobs=None)`** runs queued jobs in the test's event loop and returns their results.
- **`soniq migrations export --alembic|--sql`** prints the core migrations as an Alembic revision or as raw SQL. See the new "Migrations with Alembic" page.
- **`soniq._deprecation`** with `warn_deprecated()`, `deprecated_param()` and `SoniqDeprecationWarning`. `SONIQ_DEPRECATIONS=error|ignore` controls them. Nothing is deprecated yet.
- **Docs:** "Compare tools", "Benchmarks", "Stability and versioning", "Deprecations" and "Migrations with Alembic" pages. A throughput benchmark script in `bench/`.
- **Examples:** `examples/fastapi_demo/` (FastAPI, SQLAlchemy and Soniq with Docker Compose) and `examples/backgroundtasks_vs_soniq/` (what happens to queued work across a restart).

### Changed

- **Dashboard writes always need `SONIQ_DASHBOARD_API_KEY`.** The localhost exemption is gone. A reverse proxy on the same host (nginx's default `proxy_pass http://127.0.0.1:6161`) arrives from loopback with a loopback `Host` header, so it let anyone through.
- **`enqueue` and `enqueue_many` check arguments up front.** For a registered job without a `validate=` model, the kwargs must fit the handler's signature (`JobContext` parameters are skipped). Args must also be JSON. Both raise `SoniqError` with `SONIQ_TASK_ARGS_INVALID`. Before, a typo failed in the worker on every retry, and a `datetime` or `UUID` reached the handler as a string.
- **`get_job` finds dead-lettered jobs.** It returns them with `status` `"dead_letter"` and a `dead_letter_reason` instead of `None`.
- **The scheduler checks at least as often as the shortest `every=` schedule.** With the default `--check-interval 60`, an `every=timedelta(seconds=15)` job used to run about once a minute.
- The worker logs a warning when a handler returns a value that isn't JSON. Postgres and SQLite store its `str()`.
- `Soniq.job()` now has a precise return type instead of `Any`. Type checkers now check direct calls to decorated jobs against the function signature. There is no runtime change.

### Fixed

- Two `soniq scheduler` processes starting at the same time no longer register the same `@periodic` job twice. The duplicate row made the job fire twice per interval.
- With the pytest plugin's `soniq` marker, `my_job.enqueue(...)` and your module-level `soniq.enqueue(...)` now go to the fixture. They used to write to the real database from the instance's `database_url`.
- `enqueue(connection=conn)` no longer fails with `DataError: expected str, got dict` when `conn` comes from your own asyncpg pool or `asyncpg.connect()`. Soniq no longer needs its JSONB codec on the connection. Stored data is unchanged.
- `soniq dead-letter cleanup --dry-run` deleted the jobs anyway. It now prints how many it would remove and deletes nothing.
- `soniq scheduler` now stops cleanly on `SIGTERM` (Docker, Kubernetes, systemd), the same as on `Ctrl+C`. It used to die without stopping the scheduler loop.
- The CLI no longer emits a `DeprecationWarning` for `asyncio.iscoroutinefunction` on Python 3.14.
- The CLI no longer prints the database password. Messages such as "Using instance-based configuration: ..." show `user:***@host`, and a `?password=` query parameter is masked too.
- The `soniq scheduler` startup line shows the interval it actually uses. With a short `every=` schedule it reads "checking every 5s, lowered from 60s to keep up with the shortest every= schedule" instead of claiming 60s.

## [0.0.5]

### Added

- **`SONIQ_JOBS_MODULES` can now be set in a `.env` file**, not just exported as an environment variable. Resolution order is unchanged otherwise: an exported env var still wins over `.env`, and `--jobs-modules` still appends on top. The `.env` file is read from the current working directory, so it picks up the operator's project file rather than one bundled with the package.

## [0.0.4]

### Added

- **`--jobs-modules` on `soniq dashboard` and `soniq dead-letter`.** Dead-letter replay re-inserts a `soniq_jobs` row and needs the target job's registration loaded to resolve its retry limits, so it now runs on the same instance your job modules registered on (like `worker` / `scheduler`). `soniq dead-letter replay` fails fast with guidance when no modules are configured, and `soniq dashboard` warns at startup. The flag merges with `SONIQ_JOBS_MODULES` and can be combined with `--database-url`.
- **`sqlite:///` URLs** are now accepted in `SONIQ_DATABASE_URL` and `--database-url`, following the SQLAlchemy convention (`sqlite:///x.db` relative, `sqlite:////tmp/x.db` absolute).
- **Scheduler logs each dispatched recurring job** at INFO: job name, resulting job id, and next run time.

### Fixed

- **`soniq dead-letter replay` reports per-job results** (`Replayed <id> as <new-id>`) and exits non-zero when a job can't be replayed, instead of silently returning success.
- **SQLite and in-memory backends raise a clear error on transactional enqueue** (`app.backend.acquire()` or `enqueue(..., connection=...)`) pointing at the PostgreSQL backend, instead of an opaque `AttributeError`.

## [0.0.3]

### Fixed

- **`soniq worker` now runs jobs on the same `Soniq` instance the handlers registered on.** The worker previously built a fresh instance with an empty registry, so every discovered job dead-lettered as "not registered". It now resolves the instance the imported job modules actually declared their handlers on. When several instances are found, the one with registered jobs wins; a genuine tie raises `AmbiguousAppError` with guidance.

### Added

- **`soniq scheduler --jobs-modules`** (and automatic `SONIQ_JOBS_MODULES` import). The scheduler previously never imported job modules, so it couldn't see any `@app.periodic` definitions. It now imports them the same way the worker does and warns when no modules are configured.
- **`--database-url` conflict is now a hard error** when it disagrees with `SONIQ_DATABASE_URL`, instead of silently running against the wrong database. URLs that differ only cosmetically (scheme spelling, host casing, default port, trailing slash) are treated as equal.

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
