---
description: Set up the Soniq web dashboard for PostgreSQL-backed Python background jobs. Covers install, read-only and write mode, API key, FastAPI mounting and endpoints.
---

# Soniq dashboard: monitor queues, workers and failed jobs

The Soniq dashboard is an optional web UI that shows your job queues, workers and job history from PostgreSQL. Start it with `soniq dashboard`, or mount it inside a FastAPI app. You do not need to query the database by hand to monitor your Python background jobs.


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
pip install "soniq[dashboard]"
```

This pulls in FastAPI and Uvicorn.

### 2. Start the dashboard

```bash
soniq dashboard --port 6161
```

Open `http://localhost:6161`. It binds to `127.0.0.1` by default. To reach it from
other machines pass `--host 0.0.0.0`, and set `SONIQ_DASHBOARD_API_KEY` first.

Without an API key the dashboard only answers requests whose `Host` header is `localhost`,
`127.0.0.1` or `[::1]` (any port). This stops a web page you visit from rebinding its own
hostname to your machine and reading job arguments and results. If you run a keyless
dashboard behind a real hostname, list it in `SONIQ_DASHBOARD_ALLOWED_HOSTS`
(comma-separated, no ports), for example `SONIQ_DASHBOARD_ALLOWED_HOSTS=jobs.internal.example`.
With an API key set the check is off, so you can use any hostname.


## Read-only vs write mode

By default the dashboard is read-only. All mutating actions (replay, cancel, delete)
are disabled in the UI.

To enable write actions:

```bash
export SONIQ_DASHBOARD_WRITE_ENABLED=true
export SONIQ_DASHBOARD_API_KEY="$(openssl rand -hex 32)"
```

Write mode needs both variables; with only the first, the page stays read-only. Once the key is set, every request (reads too)
must send it in the `X-API-Key` header. To hand someone a working link, put the key in the fragment (`https://host/#api_key=<key>`): browsers never send the fragment to the server, and the page saves the key and clears it from the address bar.
The HTML page itself and CORS preflight requests load without the key, also when mounted under a prefix. The page asks for the key the first time and keeps it for the browser tab.
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
| Delete a dead-letter row | `soniq dead-letter delete <id>` | not available |
| Delete a live job | not available | Delete button (write mode) |
| Job details | not available | Click any job row |

The CLI is better for scripting and automation. The dashboard is better for
browsing and investigating.


## API endpoints

The dashboard exposes a JSON API alongside the HTML interface. All endpoints are
relative to the dashboard mount path. The running server also serves the full
generated schema at `GET /openapi.json` (and Swagger UI at `/docs`) - that is
the source of truth if this table drifts. With `SONIQ_DASHBOARD_API_KEY` set, only
the HTML page is public: `/docs` and `/openapi.json` also answer `401` without the key.

### Read endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | HTML dashboard page. |
| `GET` | `/api/stats` | Aggregate job counts by status. Returns `dict[str, int]`. |
| `GET` | `/api/jobs?queue=...&limit=...` | Most recent jobs, optionally for one queue. Returns `list[dict]`. |
| `GET` | `/api/jobs/{job_id}` | Single job details. Dead-lettered jobs are found too, with status `dead_letter`. Returns `dict`. |
| `GET` | `/api/queues` | Per-queue statistics. Returns `list[dict]`. |
| `GET` | `/api/metrics?hours=24` | Finished jobs in the window. See [Metrics](#metrics). Returns `dict`. |
| `GET` | `/api/workers` | Registered workers with their last heartbeat. Returns `list[dict]`. |
| `GET` | `/api/workers/stats` | Worker counts by status. Returns `dict`. |
| `GET` | `/api/jobs/timeline?hours=24` | Jobs per UTC hour: enqueued, completed and dead-lettered. Returns `list[dict]`. |
| `GET` | `/api/jobs/types` | Per-job-type statistics. Returns `list[dict]`. |
| `GET` | `/api/jobs/search?query=...&status=...&queue=...&job_name=...&limit=...&offset=...` | Search jobs by name or error text, with filters and paging. Returns `{"jobs": [...], "total_count": n, "has_more": bool, ...}`. |
| `GET` | `/api/dead-letter?limit=...&offset=...` | Dead-letter jobs, newest first. Returns `dict`. |
| `GET` | `/api/system/health` | System health check. Returns `dict`. |
| `GET` | `/api/tasks/drift?window_minutes=60` | Tasks registered on the running app compared with tasks seen in the database. `window_minutes` is 1 to 10080. |
| `GET` | `/api/panels` | Available dashboard panels. |
| `GET` | `/api/panels/{panel_id}` | Render one panel and return its `id`, `title` and `content`. `404` if the panel isn't registered. |

### Metrics

A job counts once it has finished: it is either `done` in `soniq_jobs` or it was
moved to the dead-letter table. Queued, running and cancelled jobs don't count.
The success rate is done jobs divided by finished jobs, and a retry that later
succeeds counts as a success. Job types use the same rule over all time, include job names whose jobs all
failed, and show the 50 busiest names. Average run time is measured from when a
job was enqueued to when it finished, so it includes time spent waiting in the
queue (jobs don't record when processing started). The health error rate is
dead-lettered jobs divided by finished jobs over the last hour.

Paging and window parameters are validated: `limit` is 1 to 1000, `offset` is 0 or
more, and `hours` is 1 to 8760. Anything else returns `422`. Search covers live
jobs only; dead-lettered jobs are under Failed.

### Write endpoints

These return `403 Forbidden` with a `{"detail": "..."}` body explaining that
writes are disabled unless `SONIQ_DASHBOARD_WRITE_ENABLED=true` and
`SONIQ_DASHBOARD_API_KEY` are both set. Callers pass the key in the
`X-API-Key` header.

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/dead-letter/{dead_letter_id}/replay` | Replay a dead-letter job (mints a new `soniq_jobs` row). |
| `POST` | `/api/jobs/{job_id}/cancel` | Cancel a queued job. |
| `DELETE` | `/api/jobs/{job_id}` | Delete a job entirely. `409` if it is running, `404` if it doesn't exist. |


Plugin panels render their HTML as-is (via `innerHTML`) because plugins are trusted server-side code.

## Configuration reference

| Env var | Default | Description |
|---|---|---|
| `SONIQ_DASHBOARD_WRITE_ENABLED` | `false` | Enable replay/cancel/delete actions. |
| `SONIQ_DASHBOARD_API_KEY` | (unset) | Required for write mode. When set, every request must send it. |
| `SONIQ_DASHBOARD_ALLOWED_HOSTS` | (unset) | Extra hostnames a keyless dashboard accepts in the `Host` header, comma-separated. |
| `SONIQ_DASHBOARD_ALLOWED_ORIGINS` | `http://localhost:6161`, `http://127.0.0.1:6161` | Origins allowed by CORS, comma-separated. |
| `SONIQ_DATABASE_URL` | `postgresql://postgres@localhost/soniq` | Database the dashboard reads from. |

CLI flags for `soniq dashboard`:

| Flag | Default | Description |
|---|---|---|
| `--host` | `127.0.0.1` | Bind address. |
| `--port` | `6161` | Bind port. |
| `--database-url` | (from env) | Override database URL. |
| `--jobs-modules` | (from env) | Modules to import on startup, merged with `SONIQ_JOBS_MODULES`. Dead-letter replay needs them. |

`soniq dashboard` also accepts the app as `APP` or `--app module:attribute`. See [Pointing a command at your app](../cli/commands.md#pointing-a-command-at-your-app).

## Next steps

- [CLI commands](../cli/commands.md) for the `soniq dashboard` flags
- [Dead-letter queue](../reference/dead-letter.md) for what replay does
- [Going to production](../production/going-to-production.md) for deployment advice
