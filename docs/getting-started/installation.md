# Installation

## Requirements

Python 3.10 or later.

## Core install (batteries-included)

```bash
pip install soniq
```

This pulls in `asyncpg` (PostgreSQL driver), `pydantic-settings`,
`croniter` (so `@periodic` and the recurring scheduler work out of the
box), and `prometheus_client` (so `PrometheusMetricsSink` is importable).
Enough to run jobs, schedules, and metrics on PostgreSQL right away.

The scheduler and Prometheus sink stay dormant unless you wire them: the
scheduler only runs if you start it, and the default `MetricsSink` is
`NoopMetricsSink`.

## Optional extras

Install only what you need:

```bash
pip install soniq[sqlite]       # aiosqlite -- SQLite backend for local dev
pip install soniq[webhooks]     # aiohttp + cryptography -- HTTP callbacks and payload signing
pip install soniq[dashboard]    # fastapi + uvicorn -- web dashboard
pip install soniq[logging]      # structlog -- structured JSON logging
pip install soniq[full]         # everything above
```

Combine extras freely: `pip install soniq[sqlite,dashboard]`.

## Backend auto-detection

Soniq picks the storage backend from your `database_url`:

| URL pattern | Backend | Driver |
|---|---|---|
| `postgresql://...` or `postgres://...` | PostgreSQL | asyncpg (included) |
| `*.db`, `*.sqlite`, `*.sqlite3` | SQLite | aiosqlite (extra) |
| `backend="memory"` | In-memory | none |

```python
from soniq import Soniq

# PostgreSQL -- production
app = Soniq(database_url="postgresql://localhost/myapp")

# SQLite -- local dev, no server
app = Soniq(database_url="local.db")

# In-memory -- unit tests
app = Soniq(backend="memory")
```

**PostgreSQL** is the only production-grade backend. It supports multiple concurrent workers, instant job delivery via `LISTEN/NOTIFY`, and transactional enqueue (enqueue a job inside your application's database transaction so the job only exists if the transaction commits).

**SQLite** is single-worker, polling-only, and doesn't support transactional enqueue. Good for prototyping and simple single-process deployments.

**Memory** stores jobs in a Python dict. Useful for unit tests where you don't want any external dependencies.

## Optional features

Optional capabilities are activated by running the matching process or by installing the right extra. Nothing else needs a feature flag:

| Capability | How to turn it on |
|---|---|
| Dead-letter queue | Always on. Failed jobs that exhaust retries land in `soniq_dead_letter_jobs`. |
| Per-job timeouts | Always on (default `SONIQ_JOB_TIMEOUT=300` seconds). Override per-job with `@app.job(timeout=...)` or set `SONIQ_JOB_TIMEOUT=0` to disable. |
| Recurring jobs | Run `soniq scheduler` alongside your worker. |
| Web dashboard | `pip install soniq[dashboard]` and run `soniq dashboard`. Dashboard mutations require `SONIQ_DASHBOARD_WRITE_ENABLED=true`. |
| HTTP webhooks | `pip install soniq[webhooks]` and configure `app.webhooks`. |
| Prometheus metrics | Wire a `PrometheusMetricsSink` on the `Soniq(...)` constructor. The default sink is a no-op. |

## Job module discovery

Soniq workers run in a separate process from the code that defines your jobs. When a worker starts, it has no idea what jobs exist -- it has to be told which Python modules to import so the `@app.job()` decorators run and the job registry gets populated.

`SONIQ_JOBS_MODULES` is that instruction.

### Setting it

```bash
# Single module (most apps)
export SONIQ_JOBS_MODULES="app.jobs"

# Multiple modules
export SONIQ_JOBS_MODULES="app.jobs,billing.tasks,notifications.handlers"
```

The current working directory is added to `sys.path` automatically, so running `soniq worker` from your project root is enough. No `PYTHONPATH` gymnastics required.

### Single repo vs cross-service

**Same repo as the worker.** Just list the dotted module path: `SONIQ_JOBS_MODULES="app.jobs"`. Standard Python import resolution applies.

**Cross-service / shared package.** If `payments.handlers` lives in a separate repo, the env var alone is not enough -- the package has to be installed in the worker's Python environment. Either `pip install` it as a wheel, mount it as a path dependency, or vendor the code. **The env var only controls *which* modules to import, not *whether* they exist.**

### Failure modes

- **Variable unset on the CLI worker.** `soniq worker` exits with a clear error: `Error: SONIQ_JOBS_MODULES is not set.`
- **Module not importable.** The worker raises an `ImportError` at startup -- check spelling, package install, and that the directory you're running from contains the module.
- **Module imports but a specific job is missing.** The job was renamed or removed but the enqueue side still references the old name. Workers will reject those rows as unknown task names; check `enqueue_validation` settings.

### Per-worker overrides

Most teams set `SONIQ_JOBS_MODULES` once globally. If you run heterogeneous workers (e.g. one fleet for media processing, another for billing), pass `--jobs-modules` on the CLI to add modules for that specific worker process. The flag *merges* with the env var:

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

See [quickstart.md](../quickstart.md) to run your first job end-to-end.
