---
description: Reference for every Soniq CLI command and flag - setup, worker, scheduler, dashboard, status, inspect, migrations export, dead-letter and tasks-check.
---

# Soniq CLI commands

The `soniq` command runs your PostgreSQL-backed Python background jobs. This page lists every command, flag and default.

Every command except `migrations` accepts `--database-url URL`. The flag overrides the `SONIQ_DATABASE_URL` environment variable.

```
soniq <command> [options]
```

Two options work on the top-level command: `--version` prints the Soniq version, and `--plugins NAMES` loads the named plugins (same as `SONIQ_PLUGINS`). Both come before the command name.

## Pointing a command at your app

By default, the CLI reads `SONIQ_DATABASE_URL` and `SONIQ_JOBS_MODULES`. You
can also name your `Soniq` instance directly. Use the form `module:attribute`:

```python
# myapp/jobs.py
from soniq import Soniq

soniq = Soniq(database_url="postgresql://localhost/myapp")
```

```bash
soniq setup myapp.jobs:soniq
soniq worker myapp.jobs:soniq --concurrency 4
```

- `worker`, `setup`, `scheduler`, `status`, `inspect`, `dashboard` and
  `migrate-status` accept the app as an optional positional argument `APP`.
- All of those and `dead-letter` accept the same value with
  `--app module:attribute`. `dead-letter` also reads a lone positional `APP` for `list`, `cleanup` and `export`.
- `tasks-check` and `migrations export` do not accept an app.
- If you give only a module (`myapp.jobs`), Soniq finds the instance in that
  module.

The command imports the module and uses that instance, with its settings.
The database URL comes from the first of these that is set:

1. `--database-url`
2. `SONIQ_DATABASE_URL`
3. The URL of the instance

The app module is imported first. `SONIQ_JOBS_MODULES` and `--jobs-modules`
add more modules to it.

If the module does not exist, the attribute does not exist, or the attribute
is not a `Soniq` instance, the command stops with an error.


## setup

Create or update the database schema. The command is idempotent, so you can run it on every deploy.

```bash
soniq setup [APP]
```

The command does these steps:
1. Counts the core migrations and lists the pending ones.
2. Applies the pending migrations.
3. Reports how many migrations it applied.

The database must already exist. If it does not, the command prints a hint to run `createdb`. If the schema is already up to date, the command prints a confirmation and exits.

**When to use:** during deployment, in CI pipelines, or the first time you set up
Soniq.


## worker

Start a worker process that fetches and executes jobs.

```bash
soniq worker [APP] [--concurrency N] [--queues QUEUES] [--jobs-modules MODULES] [--run-once] [--log-level LEVEL]
```

| Flag | Type | Default | Description |
|---|---|---|---|
| `--concurrency` | `int` | `4` | Number of jobs the worker will run in parallel. |
| `--queues` | `str` | all queues | Comma-separated list of queue names to process. |
| `--jobs-modules` | `str` | | Comma-separated list of modules to import on startup. Merged with `SONIQ_JOBS_MODULES` (the env var sets the base; this flag adds more) for per-worker overrides. |
| `--run-once` | flag | off | Process all available jobs and exit. |
| `--log-level` | `str` | `INFO` | Root logger level. Falls back to `SONIQ_LOG_LEVEL` when the flag is not set. |

The worker needs `SONIQ_JOBS_MODULES` (or `APP`, see [Pointing a command at your app](#pointing-a-command-at-your-app)) to discover and import
your job functions. See [Job module discovery](../getting-started/installation.md#job-module-discovery) for the full reference (single-repo, cross-service, and per-worker overrides).

```bash
export SONIQ_JOBS_MODULES=myapp.tasks,myapp.other_tasks
soniq worker --concurrency 8 --queues urgent,default
```

When you omit `--queues`, the worker processes all queues in the database. Pass `--queues=name1,name2` to restrict it.

The worker handles `SIGINT`, `SIGTERM` and `SIGHUP` as a request for graceful shutdown. It stops fetching jobs and waits for running jobs to finish, up to `shutdown_timeout` (30 seconds by default). A second signal does not force an exit.


## status

Show system health, queue statistics, and optionally recent jobs.

```bash
soniq status [APP] [--verbose] [--jobs]
```

| Flag | Type | Default | Description |
|---|---|---|---|
| `--verbose` | flag | off | Show per-queue breakdown table. |
| `--jobs` | flag | off | Show the 10 most recent jobs. |

```bash
soniq status --verbose --jobs
```

Output includes:
- Database connection health check
- Total jobs, queued, processing, done, cancelled and dead-letter counts
- Active and stale worker summary
- Per-queue breakdown (with `--verbose`)
- Recent job list (with `--jobs`)


## inspect

List registered workers and recurring schedules.

```bash
soniq inspect [APP] [--stale] [--cleanup] [--schedules]
```

| Flag | Type | Default | Description |
|---|---|---|---|
| `--stale` | flag | off | Include stale (no recent heartbeat) workers in the output. |
| `--cleanup` | flag | off | Remove stale worker records from the database. |
| `--schedules` | flag | off | List each recurring schedule (name, status, next run). Without this flag, only the active/paused counts are shown. |

```bash
# Show active workers + schedule summary (counts only)
soniq inspect

# Show stale workers too
soniq inspect --stale

# Clean up stale records
soniq inspect --cleanup

# List every recurring schedule
soniq inspect --schedules
```

For each active worker, the command shows hostname, PID, queues, concurrency, uptime, last
heartbeat, and resource usage (CPU and memory) when available.

The output also includes a **Recurring Schedules** section with the count of
active and paused schedules registered via `@soniq.periodic(...)` or
`soniq.scheduler.add(...)`. The scheduler elects a leader on every tick and keeps no process record. So `inspect` reports which schedules are registered, not which scheduler process is the leader. To check that a scheduler process is up, use your process supervisor (systemd, Kubernetes, Docker).


## dead-letter

Manage jobs that exhausted all retries.

```bash
soniq dead-letter <action> [options]
```

### Actions

**list** - show dead-letter jobs.

```bash
soniq dead-letter list [--limit 50] [--filter JOB_NAME]
```

**replay** - mint a fresh job from a dead-letter row. The DLQ row stays
as the audit trail; a new `soniq_jobs` row is created with a new UUID
and `resurrection_count` is incremented.

```bash
soniq dead-letter replay <job-id> [<job-id> ...] --jobs-modules myapp.tasks
soniq dead-letter replay --all                       # interactive prompt for >= 5 jobs
soniq dead-letter replay --all --dry-run             # report count + sample, no changes
soniq dead-letter replay --all --yes                 # skip the confirmation prompt
```

`replay` needs your job modules loaded to resolve the target job's retry
limits. Load them with `--app`, `--jobs-modules` (merged with `SONIQ_JOBS_MODULES`,
same as `worker` and `scheduler`) or `SONIQ_JOBS_MODULES`. `replay` exits
non-zero if none of these is set. The actions `list`, `delete`,
`cleanup` and `export` do not need job modules.

Use `replay --all` with care. If the dead-letter queue filled up because of a bug that
you have not fixed, replaying every job runs the same jobs into the same bug. The CLI prompts before it re-queues five or more jobs
and refuses to run in a non-interactive shell without `--yes`.

**delete** - permanently remove a dead-letter job.

```bash
soniq dead-letter delete <job-id> [<job-id> ...]
soniq dead-letter delete --all                       # interactive prompt for >= 5 jobs
soniq dead-letter delete --all --dry-run             # report count + sample, no changes
soniq dead-letter delete --all --yes                 # skip the confirmation prompt
```

**cleanup** - remove dead-letter jobs older than N days.

```bash
soniq dead-letter cleanup --days 30
soniq dead-letter cleanup --days 30 --dry-run    # report the count, no changes
```

**export** - export dead-letter jobs to a file.

```bash
soniq dead-letter export --format json --output dead_letter.json
soniq dead-letter export --format csv --output dead_letter.csv
```

### Common flags

| Flag | Type | Default | Description |
|---|---|---|---|
| `--limit` | `int` | `50` | Maximum jobs to select. Applies to `list`, and to `--all` and export filters. |
| `--filter` | `str` | | Filter by exact job name. |
| `--all` | flag | off | Apply action to all matching jobs. |
| `--days` | `int` | `30` | Age threshold for `cleanup`. |
| `--dry-run` | flag | off | Report what would happen and change nothing. Honoured on `replay --all`, `delete --all` (count + sample) and `cleanup` (count). |
| `--yes`, `-y` | flag | off | Skip the interactive confirmation for bulk `replay --all` / `delete --all`. Required in non-interactive shells. |
| `--format` | `csv \| json` | `csv` | Export format. |
| `--output` | `str` | | Output file path. `export` writes a timestamped file in the current directory if you omit it. |
| `--jobs-modules` | `str` | | Comma-separated modules to import (for `replay`). Merged with `SONIQ_JOBS_MODULES`. |


## dashboard

Launch the web dashboard for monitoring jobs, queues, and workers.

The dashboard needs `pip install "soniq[dashboard]"`.

```bash
soniq dashboard [APP] [--host HOST] [--port PORT] [--jobs-modules MODULES]
```

| Flag | Type | Default | Description |
|---|---|---|---|
| `--host` | `str` | `127.0.0.1` | Network interface to bind to. Use `0.0.0.0` for all interfaces. |
| `--port` | `int` | `6161` | Port number. |
| `--jobs-modules` | `str` | | Comma-separated modules to import on startup. Merged with `SONIQ_JOBS_MODULES`. Dead-letter replay needs the target job's registration loaded, so run the dashboard on the same instance your job modules registered on. |

```bash
soniq dashboard --host 0.0.0.0 --port 6161
```

Open `http://localhost:6161` in your browser.

The dashboard is read-only by default. Set `SONIQ_DASHBOARD_WRITE_ENABLED=true`
and `SONIQ_DASHBOARD_API_KEY` to enable replay, delete, and cancel actions. Dead-letter replay also
needs your job modules loaded (`--jobs-modules` or `SONIQ_JOBS_MODULES`) so Soniq can resolve the
replayed job's retry limits, exactly like `soniq dead-letter
replay`.

Without `SONIQ_DASHBOARD_API_KEY` the dashboard rejects any `Host` header other than
`localhost`, `127.0.0.1` or `[::1]`. To serve a keyless dashboard under another hostname, set
`SONIQ_DASHBOARD_ALLOWED_HOSTS` to a comma-separated list of hostnames.


## scheduler

Start the recurring job scheduler. It checks for due `@periodic` jobs and
enqueues them.

```bash
soniq scheduler [APP] [--check-interval SECONDS] [--jobs-modules MODULES] [--log-level LEVEL]
```

| Flag | Type | Default | Description |
|---|---|---|---|
| `--check-interval` | `int` | `60` | Seconds between checks for due recurring jobs. Lowered automatically to the shortest `every=` interval. |
| `--jobs-modules` | `str` | | Comma-separated list of modules to import on startup. Merged with `SONIQ_JOBS_MODULES` (adds to, does not replace). The scheduler must import your `@soniq.periodic` definitions to know what to fire. |
| `--log-level` | `str` | `INFO` | Root logger level. Falls back to `SONIQ_LOG_LEVEL` when the flag is not set. |

```bash
# Start the scheduler
soniq scheduler --check-interval 30
```

Stop the scheduler with `Ctrl+C` or `SIGTERM`.

To inspect registered schedules, use `soniq inspect` (it includes a
**Recurring Schedules** section) or `soniq inspect --schedules` to list each
one by name.


## migrate-status

Show which database migrations have been applied and which are pending.

```bash
soniq migrate-status [APP]
```

Output lists each migration with its status (applied or pending). If migrations
are pending, the command tells you to run `soniq setup`.


## migrations export

Print Soniq's core migrations as an Alembic revision or as plain SQL. Use it when another tool owns your schema. The command needs no database connection.

```bash
soniq migrations export (--alembic | --sql) [--from VERSION] [--down-revision REV] [--revision ID]
```

| Flag | Type | Default | Description |
|---|---|---|---|
| `--alembic` | flag | | Print an Alembic revision file. Pick this or `--sql`. |
| `--sql` | flag | | Print raw SQL. Pick this or `--alembic`. |
| `--from` | `str` | all migrations | Export only migrations newer than this version, for example `0003`. |
| `--down-revision` | `str` | `None` | Alembic `down_revision` for the generated file. |
| `--revision` | `str` | `soniq_<last version>` | Alembic revision id. |


## tasks-check

Compare the `TaskRef` declarations in a cross-service stub package against
`soniq_task_registry`, the table every worker fills with its registered
task names on startup. Use it as a CI gate before a deploy. If a producer's
stubs and the consumer's handlers have drifted apart, the check fails
before the bad enqueue reaches production.

```bash
soniq tasks-check <package> [--database-url URL]
```

`<package>` is a stub package directory or a dotted module path holding the
`task_ref(...)` declarations. The command needs `SONIQ_DATABASE_URL` (or `--database-url`)
to read the registry table.

It reports drift in both directions and exits non-zero if either list is
non-empty:

- **TaskRef with no registered task** - the stub package declares a name that
  no running worker has registered. Either the consumer has not deployed the
  handler yet, or the name is misspelled in the stub.
- **Registered task with no TaskRef** - a worker registered a task name that
  the stub package does not declare. The stub is stale, or the handler was
  renamed without updating the producers.

On a clean match it prints an `OK` line and exits `0`.


## Next steps

- [Run a worker in production](../production/deployment.md)
- [Dashboard overview](../dashboard/overview.md)
- [Dead-letter queue](../reference/dead-letter.md)
