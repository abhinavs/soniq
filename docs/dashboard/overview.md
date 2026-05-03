# Dashboard

The Soniq dashboard is an optional web UI for real-time visibility into your
job queues, workers, and job history. It replaces the need to query the database
directly for operational monitoring.


## What you can see

- **Queue overview** -- job counts by status (queued, processing, done, dead-letter, cancelled) for each queue.
- **Worker status** -- active workers, their concurrency, uptime, last heartbeat, and resource usage.
- **Job inspection** -- search, filter, and view individual job details including arguments, result, error messages, and attempt history.
- **Dead-letter queue** -- browse failed jobs, inspect errors, and (in write mode) retry or delete them.
- **Metrics** -- job throughput timeline, per-job-type statistics, and system health.


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
```

Keep write mode disabled in production unless the dashboard is behind authentication.


## Mounting inside a FastAPI app

Instead of running the dashboard as a standalone process, you can mount it as a
sub-application inside your existing FastAPI app:

```python
from fastapi import FastAPI
from soniq import Soniq
from soniq.dashboard import create_dashboard_app

soniq_app = Soniq(database_url="postgresql://localhost/myapp")

app = FastAPI()
app.mount("/admin/queue", create_dashboard_app(soniq_app))
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
relative to the dashboard mount path.

### Read endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | HTML dashboard page. |
| `GET` | `/api/stats` | Aggregate job counts by status. Returns `dict[str, int]`. |
| `GET` | `/api/jobs?status=...&queue=...&limit=...&offset=...` | Recent jobs with optional filters. Returns `list[dict]`. |
| `GET` | `/api/jobs/{job_id}` | Single job details. Returns `dict`. |
| `GET` | `/api/queues` | Per-queue statistics. Returns `list[dict]`. |
| `GET` | `/api/metrics?hours=24` | Job metrics for the given time range. Returns `dict`. |
| `GET` | `/api/workers/stats` | Worker status and health. Returns `dict`. |
| `GET` | `/api/jobs/timeline?hours=24` | Job throughput timeline. Returns `list[dict]`. |
| `GET` | `/api/jobs/types` | Per-job-type statistics. Returns `list[dict]`. |
| `GET` | `/api/jobs/search?q=...&status=...&queue=...` | Search jobs by name, status, or queue. Returns `list[dict]`. |
| `GET` | `/api/system/health` | System health check. Returns `dict`. |
| `GET` | `/api/tasks/drift` | Tasks registered on the running app vs. tasks observed in the DB. |
| `GET` | `/api/panels` | Available dashboard panels. |
| `GET` | `/api/panels/{panel_id}` | Single panel descriptor. |

### Write endpoints

These return `403 Forbidden` unless `SONIQ_DASHBOARD_WRITE_ENABLED=true`.

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/dead-letter/{dead_letter_id}/replay` | Replay a dead-letter job (mints a new `soniq_jobs` row). |
| `POST` | `/api/jobs/{job_id}/cancel` | Cancel a queued job. |
| `DELETE` | `/api/jobs/{job_id}` | Delete a job entirely. |


## Configuration reference

| Env var | Default | Description |
|---|---|---|
| `SONIQ_DASHBOARD_WRITE_ENABLED` | `false` | Enable replay/cancel/delete actions. |
| `SONIQ_DASHBOARD_API_KEY` | (unset) | Required for non-loopback callers when write mode is on. |
| `SONIQ_DATABASE_URL` | `postgresql://postgres@localhost/soniq` | Database the dashboard reads from. |

CLI flags for `soniq dashboard`:

| Flag | Default | Description |
|---|---|---|
| `--host` | `127.0.0.1` | Bind address. |
| `--port` | `6161` | Bind port. |
| `--reload` | off | Auto-reload on file changes. |
| `--database-url` | (from env) | Override database URL. |
