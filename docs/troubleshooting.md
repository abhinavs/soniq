---
description: Fix common Soniq problems - idle workers, stuck jobs, a scheduler that does not fire, enqueue errors, dead-letter jobs and pool size errors.
---

# Troubleshooting Soniq workers, enqueue errors and the scheduler

Each entry has three parts: the symptom, the likely cause, and the fix. Each heading is the message or symptom as you see it.

## Worker problems

### Worker starts but never processes jobs

**Likely cause:** `SONIQ_JOBS_MODULES` is not set, or it names a module that does not exist or fails to import on the worker host. If you start the worker with an app path such as `soniq worker myapp.jobs:soniq`, the module in the path must import.

**Fix:**

```bash
echo $SONIQ_JOBS_MODULES                                  # confirm it's set
python -c "import app.jobs"                               # confirm the module imports
```

If the module imports locally but not on the worker, the worker host does not have your code. Put the code in the container image, mount it as a volume, or install it with `pip`. The variable only names the module. It does not supply the code.

See [Job module discovery](getting-started/installation.md#job-module-discovery) for details.

### `Error: SONIQ_JOBS_MODULES is not set and --jobs-modules was not passed`

**Likely cause:** the worker has no module to import. You started `soniq worker` with no app path, no `--jobs-modules` flag and no `SONIQ_JOBS_MODULES` variable. The worker exits with code 1.

**Fix:** set the variable, or pass an app path.

```bash
SONIQ_JOBS_MODULES=app.jobs soniq worker
soniq worker app.jobs:soniq
```

### Jobs stuck in `processing` after a worker restart

**Likely cause:** the worker was killed with `SIGKILL` or crashed (out of memory, container eviction) before it could return its running jobs to `queued`.

**Fix:** wait for the periodic cleanup. A running worker checks every 5 minutes (`SONIQ_CLEANUP_INTERVAL`). It puts back the jobs of workers that sent no heartbeat for 5 minutes (`SONIQ_HEARTBEAT_TIMEOUT`). To run the cleanup now:

```bash
soniq inspect --cleanup
```

In production, rely on the periodic cleanup, or run `soniq inspect --cleanup` from a cron job.

### Worker logs `Failed to set up LISTEN/NOTIFY`

**Likely cause:** the worker connects through PgBouncer in transaction-pooling mode. Transaction pooling does not keep `LISTEN` registrations between statements.

**Fix:** switch PgBouncer to session pooling for the worker connection. You can also accept polling. The worker then checks for jobs every `SONIQ_POLL_INTERVAL` seconds (default 5). Jobs still run, but the worker takes longer to pick them up.

## Recurring scheduler problems

### Recurring jobs are not firing

**Likely cause #1:** the scheduler is not running. `soniq worker` does not run the scheduler. The scheduler is a separate process.

**Fix:**

```bash
SONIQ_JOBS_MODULES=app.jobs soniq scheduler
```

**Likely cause #2:** the scheduler is running but cannot acquire its advisory lock because PgBouncer is in transaction-pooling mode.

**Fix:** the scheduler uses a PostgreSQL advisory lock to elect one leader across replicas. Advisory locks belong to a session, and transaction pooling breaks them. Use session pooling or a direct PostgreSQL connection for the scheduler.

### A second `soniq scheduler` process runs but fires nothing

This is expected. You can run several `soniq scheduler` processes for redundancy. Only the leader fires jobs. The other processes wait, and one of them takes over if the leader stops.

## Enqueue problems

### `enqueue(..., connection=conn)` raises `ValueError`

**Likely cause:** transactional enqueue needs the PostgreSQL backend. The SQLite and memory backends do not support it.

**Fix:** use a `postgresql://...` URL, or remove `connection=conn` in non-PostgreSQL environments.

### `SONIQ_UNSUPPORTED_CONNECTION` when passing a SQLAlchemy session

**Likely cause #1:** the engine uses a driver other than asyncpg or psycopg 3, for example `aiosqlite`.

**Likely cause #2:** the session, SQLAlchemy connection or asyncpg connection has no active transaction. The job would not be atomic with your writes.

**Fix:** use a `postgresql+asyncpg://...` or `postgresql+psycopg://...` engine URL, and call `enqueue()` inside `async with session.begin():`. Pass the session as `connection=`. You do not need the raw driver connection. See [Pattern 1](guides/transactional-enqueue.md#pattern-1-pass-your-sqlalchemy-session) of the transactional enqueue guide.

### `SONIQ_UNKNOWN_TASK_NAME`: `Task '...' is not registered locally`

**Likely cause:** the code calls `enqueue("name")` with a string, but this process has no job with that name.

**Fix:** decide whether the producer must validate locally:

- If the producer also defines the job, import the module that registers it before you enqueue.
- If the producer has no local registry (cross-service setup), set `SONIQ_ENQUEUE_VALIDATION=warn` or `none`. You can also enqueue a `TaskRef` instead of a string. See [cross-service jobs](guides/cross-service-jobs.md).

### `SONIQ_TASK_ARGS_INVALID` when you enqueue a job

**Likely cause:** the arguments do not fit the job. Either an argument is not JSON (for example a `datetime`, a `UUID` or `NaN`), or the keyword arguments do not match the handler signature. You can also see this error when you pass positional arguments, because `enqueue()` accepts keyword arguments only.

**Fix:** pass keyword arguments with JSON values. Convert datetimes with `.isoformat()` and UUIDs with `str()`.

## Setup and migration problems

### `soniq setup` fails on a multi-replica deploy

**Likely cause:** `soniq setup` runs at application startup. When many replicas start together, they race to apply the same migrations.

**Fix:** run `soniq setup` once per deploy, in a place where it cannot race with itself. Use a CI step before the deploy, a Kubernetes init container, a dedicated migration job, or an entrypoint script that runs on one replica only.

### `soniq setup` succeeds but the worker cannot find the tables

**Likely cause:** the worker's `SONIQ_DATABASE_URL` points to a different database than the one `soniq setup` used.

**Fix:** print both URLs and compare them. A common mistake is a `localhost` URL for `setup` and a Docker network URL for the worker.

## Dead-letter queue

### Dead-letter jobs are not appearing

**Likely cause:** the failed jobs still have retries left. A job moves to the dead-letter queue only after its last attempt fails. A job with `retries=N` has `N + 1` attempts.

**Fix:** wait for the retries to finish, or run `soniq status --jobs` to see the state of recent jobs. Dead-letter jobs are rows in the `soniq_dead_letter_jobs` table, not in `soniq_jobs`.

### `soniq dead-letter replay --all` re-enqueues thousands of jobs

**Likely cause:** the cause of the failures is not fixed, so the replayed jobs fail again and return to the dead-letter queue. The CLI asks for confirmation before it replays 5 or more jobs, unless you pass `--yes`.

**Fix:** fix the cause first. If you already replayed the jobs, delete the dead-letter rows that fail again with `soniq dead-letter delete`.

## Pool and capacity

### `SONIQ_POOL_TOO_SMALL`: `Connection pool too small: worker concurrency (X) plus reserved headroom (Y) requires Z connections`

**Likely cause:** the pool is too small for the requested concurrency. Soniq reserves headroom for its LISTEN/NOTIFY connection and heartbeat writer.

**Fix:** raise `SONIQ_POOL_MAX_SIZE` (default 20) to at least `concurrency + SONIQ_POOL_HEADROOM` (default headroom 2). You can also lower `--concurrency`.

### Worker "hangs" with high concurrency under load

**Likely cause:** sync handlers use all threads of the bounded thread pool, so other jobs wait for a free thread.

**Fix:** change sync handlers to `async def`, or raise `SONIQ_SYNC_HANDLER_POOL_SIZE` (default 8).

## When to file an issue

Open a GitHub issue if the symptom is not here, the fix did not work, or the symptom comes back after the fix. Include:

- Soniq version (`soniq --version`)
- Postgres version (`SELECT version();`)
- The relevant config (env vars, CLI flags)
- Worker or scheduler logs from a few seconds before to a few seconds after the failure
