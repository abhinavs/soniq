# Dashboard

The Soniq dashboard is an optional web UI for real-time visibility into your
job queues, workers, and job history. It replaces the need to query the database
directly for operational monitoring.


## What you can see

- **Overview** - where your jobs are right now (queued, running, done, failed, cancelled), a 24-hour chart of finished jobs, and a warning when the database has job names your code doesn't register.
- **Jobs** - search by name or error, filter by status and queue, and open any job to see its arguments, result and last error.
- **Failed** - jobs that ran out of retries, with their tracebacks. In write mode you can replay them.
- **Queues and job types** - counts, success rates and average run time.
- **Workers** - each worker's host, concurrency and last heartbeat, marked online, stopped or not responding.

The page has light and dark themes, refreshes itself every 10 seconds while the tab is visible, and works on a phone. Press `/` to search.


## Setup

### 1. Install the dashboard extra

```bash
pip install soniq[dashboard]
```

This pulls in FastAPI and Uvicorn.

### 2. Start the dashboard

```bash
soniq dashboard --host 0.0.0.0 --port 6161
```

Open `http://localhost:6161`.


## Read-only vs write mode

By default the dashboard is read-only. All mutating actions (replay, cancel, delete)
are disabled in the UI.

To enable write actions:

```bash
export SONIQ_DASHBOARD_WRITE_ENABLED=true
export SONIQ_DASHBOARD_API_KEY="$(openssl rand -hex 32)"
```

Write mode needs both variables. Once the key is set, every request (reads too)
must send it in the `X-API-Key` header or the `?api_key=` query parameter.
The page asks for the key the first time and keeps it for the browser tab.
There is no localhost exemption, because a reverse proxy on the same host
looks exactly like a local caller.


## Mounting inside a FastAPI app

Instead of running the dashboard as a standalone process, you can mount it as a
sub-application inside your existing FastAPI app:

```python
from fastapi import FastAPI
from soniq import Soniq
from soniq.dashboard import create_dashboard_app

soniq = Soniq(database_url="postgresql://localhost/myapp")

app = FastAPI()
app.mount("/admin/queue", create_dashboard_app(soniq))
```

The dashboard is then accessible at `http://localhost:8000/admin/queue`. This is
useful when you want to serve the dashboard behind your existing auth middleware
or reverse proxy.


## CLI vs Dashboard

| Task | CLI command | Dashboard |
|---|---|---|
| Queue stats | `soniq status --verbose` | Queue overview page |
| Worker list | `soniq inspect` | Workers panel |
| Recent jobs | `soniq status --jobs` | Jobs page with search |
| Dead-letter list | `soniq dead-letter list` | DLQ tab |
| Replay a job | `soniq dead-letter replay <id>` | Replay button (write mode) |
| Delete a job | `soniq dead-letter delete <id>` | Delete button (write mode) |
| Job details | not available | Click any job row |

The CLI is better for scripting and automation. The dashboard is better for
browsing and investigating.


## API endpoints

The dashboard exposes a JSON API alongside the HTML interface. All endpoints are
relative to the dashboard mount path. The running server also serves the full
generated schema at `GET /openapi.json` (and Swagger UI at `/docs`) - that is
the source of truth if this table drifts.

### Read endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | HTML dashboard page. |
| `GET` | `/api/stats` | Aggregate job counts by status. Returns `dict[str, int]`. |
| `GET` | `/api/jobs?queue=...&limit=...` | Most recent jobs, optionally for one queue. Returns `list[dict]`. |
| `GET` | `/api/jobs/{job_id}` | Single job details. Returns `dict`. |
| `GET` | `/api/queues` | Per-queue statistics. Returns `list[dict]`. |
| `GET` | `/api/metrics?hours=24` | Finished jobs in the window. See [Metrics](#metrics). Returns `dict`. |
| `GET` | `/api/workers` | Registered workers with their last heartbeat. Returns `list[dict]`. |
| `GET` | `/api/workers/stats` | Worker counts by status. Returns `dict`. |
| `GET` | `/api/jobs/timeline?hours=24` | Jobs per UTC hour: enqueued, completed and dead-lettered. Returns `list[dict]`. |
| `GET` | `/api/jobs/types` | Per-job-type statistics. Returns `list[dict]`. |
| `GET` | `/api/jobs/search?query=...&status=...&queue=...&limit=...&offset=...` | Search jobs by name or error text, with filters and paging. Returns `{"jobs": [...], "total_count": n, "has_more": bool, ...}`. |
| `GET` | `/api/dead-letter?limit=...&offset=...` | Dead-letter jobs, newest first. Returns `dict`. |
| `GET` | `/api/system/health` | System health check. Returns `dict`. |
| `GET` | `/api/tasks/drift` | Tasks registered on the running app vs. tasks observed in the DB. |
| `GET` | `/api/panels` | Available dashboard panels. |
| `GET` | `/api/panels/{panel_id}` | Single panel descriptor. |

### Metrics

A job counts once it has finished: it is either `done` in `soniq_jobs` or it was
moved to the dead-letter table. Queued, running and cancelled jobs don't count.
The success rate is done jobs divided by finished jobs, and a retry that later
succeeds counts as a success. Job types use the same rule over all time.

### Write endpoints

These return `403 Forbidden` with a `{"detail": "..."}` body explaining that
writes are disabled unless `SONIQ_DASHBOARD_WRITE_ENABLED=true` and
`SONIQ_DASHBOARD_API_KEY` are both set. Callers pass the key in the
`X-API-Key` header.

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/dead-letter/{dead_letter_id}/replay` | Replay a dead-letter job (mints a new `soniq_jobs` row). |
| `POST` | `/api/jobs/{job_id}/cancel` | Cancel a queued job. |
| `DELETE` | `/api/jobs/{job_id}` | Delete a job entirely. |


## Configuration reference

| Env var | Default | Description |
|---|---|---|
| `SONIQ_DASHBOARD_WRITE_ENABLED` | `false` | Enable replay/cancel/delete actions. |
| `SONIQ_DASHBOARD_API_KEY` | (unset) | Required for write mode. When set, every request must send it. |
| `SONIQ_DATABASE_URL` | `postgresql://postgres@localhost/soniq` | Database the dashboard reads from. |

CLI flags for `soniq dashboard`:

| Flag | Default | Description |
|---|---|---|
| `--host` | `127.0.0.1` | Bind address. |
| `--port` | `6161` | Bind port. |
| `--reload` | off | Auto-reload on file changes. |
| `--database-url` | (from env) | Override database URL. |
