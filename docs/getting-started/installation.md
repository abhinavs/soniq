---
description: Install Soniq, the Python background job library for PostgreSQL. Covers pip extras, backend selection, job module discovery and per-worker overrides.
---

# Install Soniq and configure job modules

Soniq installs with `pip install soniq` and needs Python 3.10 or later and a PostgreSQL database. This page lists the optional extras, the supported backends, and how workers find your job code.

## Requirements

Python 3.10 or later.

## Core install

```bash
pip install soniq
```

This installs `asyncpg` (the PostgreSQL driver), `pydantic-settings`, `croniter` (for `@soniq.periodic` and the recurring scheduler), and `prometheus_client` (so `PrometheusMetricsSink` is importable). That is enough to run jobs, schedules, and metrics on PostgreSQL.

The scheduler and the Prometheus sink do nothing until you set them up. The scheduler runs only when you start it. The default `MetricsSink` is `NoopMetricsSink`.

## Optional extras

Install only the extras you need:

```bash
pip install "soniq[sqlite]"       # aiosqlite - SQLite backend for local dev
pip install "soniq[webhooks]"     # aiohttp + cryptography - HTTP callbacks and payload signing
pip install "soniq[dashboard]"    # fastapi + uvicorn - web dashboard
pip install "soniq[logging]"      # structlog - structured JSON logging
pip install "soniq[sqlalchemy]"  # SQLAlchemy async - transactional enqueue with a session
pip install "soniq[psycopg]"      # psycopg 3 - transactional enqueue with a psycopg connection
pip install "soniq[full]"         # sqlite, webhooks, logging and dashboard
```

Combine extras freely: `pip install "soniq[sqlite,dashboard]"`.

## Backend auto-detection

Soniq picks the storage backend from the `database_url` you pass.

| URL pattern | Backend | Driver |
|---|---|---|
| `postgresql://...` or `postgres://...` | PostgreSQL | asyncpg (included) |
| `*.db`, `*.sqlite`, `*.sqlite3` | SQLite | aiosqlite (extra) |
| `backend="memory"` | In-memory | none |

```python
from soniq import Soniq

# PostgreSQL - production
soniq = Soniq(database_url="postgresql://localhost/myapp")

# SQLite - local dev, no server
soniq = Soniq(database_url="local.db")

# In-memory - unit tests
soniq = Soniq(backend="memory")
```

**PostgreSQL** is the only production backend. It supports many concurrent workers, instant job delivery through `LISTEN/NOTIFY`, and transactional enqueue. Transactional enqueue adds a job inside your application's database transaction, so the job exists only if the transaction commits.

**SQLite** supports one worker and polls for jobs. It does not support transactional enqueue. Use it for prototypes and single-process deployments.

**Memory** stores jobs in a Python dict. Use it for unit tests that need no external services.

## Optional features

You turn on an optional capability by running the matching process or installing the matching extra. No feature flags exist:

| Capability | How to turn it on |
|---|---|
| Dead-letter queue | Always on. Failed jobs that exhaust retries land in `soniq_dead_letter_jobs`. |
| Per-job timeouts | Always on (default `SONIQ_JOB_TIMEOUT=300` seconds). Override per-job with `@soniq.job(timeout=...)` or set `SONIQ_JOB_TIMEOUT=0` to disable timeouts. |
| Recurring jobs | Run `soniq scheduler` alongside your worker. |
| Web dashboard | `pip install "soniq[dashboard]"` and run `soniq dashboard`. Dashboard mutations require `SONIQ_DASHBOARD_WRITE_ENABLED=true`. |
| HTTP webhooks | `pip install "soniq[webhooks]"` and configure `soniq.webhooks`. |
| Prometheus metrics | Wire a `PrometheusMetricsSink` on the `Soniq(...)` constructor. The default sink is a no-op. |

## Job module discovery

A Soniq worker runs in a separate process from the code that enqueues your jobs. When the worker starts, it does not know which jobs exist. You must tell it which Python modules to import. The import runs the `@soniq.job()` decorators and fills the job registry.

`SONIQ_JOBS_MODULES` holds that list of modules.

### Setting it

```bash
# Single module (most apps)
export SONIQ_JOBS_MODULES="app.jobs"

# Multiple modules
export SONIQ_JOBS_MODULES="app.jobs,billing.tasks,notifications.handlers"
```

Soniq adds the current working directory to `sys.path`. Run `soniq worker` from your project root and you do not need to set `PYTHONPATH`.

### Single repo vs cross-service

**Same repo as the worker.** List the dotted module path: `SONIQ_JOBS_MODULES="app.jobs"`. Standard Python import rules apply.

**Cross-service or shared package.** If `payments.handlers` lives in a separate repo, the variable alone is not enough. You must install the package in the worker's Python environment. Install it as a wheel, mount it as a path dependency, or vendor the code. The variable controls *which* modules to import. It does not make them exist.

### Failure modes

- **Variable unset on the CLI worker.** `soniq worker` exits with the error `Error: SONIQ_JOBS_MODULES is not set and --jobs-modules was not passed.` You can also pass an app path, such as `soniq worker myapp.jobs:soniq`, instead of the variable.
- **Module not importable.** The worker raises an `ImportError` at startup. Check the spelling, check that the package is installed, and check that the current directory contains the module.
- **Module imports but a job is missing.** Someone renamed or removed the job, but the enqueue side still uses the old name. The worker rejects those rows because the task name is unknown. Check the `enqueue_validation` setting.

### Per-worker overrides

Most teams set `SONIQ_JOBS_MODULES` once. If you run different worker fleets (for example, one for media processing and one for billing), pass `--jobs-modules` to add modules for one worker process. The flag *merges* with the environment variable:

```bash
# Fleet 1 - billing-focused, sees both shared and billing-specific jobs
export SONIQ_JOBS_MODULES="app.jobs"
soniq worker --queues billing --jobs-modules billing.critical,billing.fraud

# Fleet 2 - media-focused, same shared modules + media-specific
soniq worker --queues media --jobs-modules media.processing,media.thumbnails
```

## Verifying the install

```bash
# Check the version
soniq --version

# Create tables (run against your database)
SONIQ_DATABASE_URL="postgresql://localhost/myapp" soniq setup

# Start a worker
SONIQ_DATABASE_URL="postgresql://localhost/myapp" \
SONIQ_JOBS_MODULES="myapp.jobs" \
soniq worker
```

## Next steps

Follow the [quickstart](../quickstart.md) to run your first job from start to finish, or start the [tutorial](../tutorial/01-defining-jobs.md).
