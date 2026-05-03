# Troubleshooting

The shape of every entry: **symptom -> likely cause -> fix**. Symptoms are written the way you'd see them, not the way they're described internally.

## Worker problems

### Worker starts but never processes jobs

**Likely cause:** `SONIQ_JOBS_MODULES` is not set, or it points to a module that doesn't exist or fails to import on the worker host.

**Fix:**

```bash
echo $SONIQ_JOBS_MODULES                                  # confirm it's set
python -c "import app.jobs"                               # confirm the module imports
```

If it imports locally but not on the worker, the worker host probably doesn't have your code installed. Either bake the code into the container image, mount it as a volume, or `pip install` it as a package. The env var is the *instruction*, not the code itself.

See [Job module discovery](getting-started/installation.md#job-module-discovery) for the full guidance.

### `Error: SONIQ_JOBS_MODULES is not set` and the worker exits

**Likely cause:** literally what it says. The worker requires this env var to know what to import.

**Fix:** export the variable before starting the worker, or add it to the worker's environment in your process manager.

```bash
SONIQ_JOBS_MODULES=app.jobs soniq worker
```

### Jobs stuck in `processing` after a worker restart

**Likely cause:** the worker was killed with `SIGKILL` or crashed (OOM, container eviction) before it could mark its in-flight jobs back to `queued`.

**Fix:** stale workers and their jobs are recovered by the periodic cleanup that running workers perform every 5 minutes. To force it now:

```bash
soniq inspect --cleanup
```

In production, either rely on the periodic cleanup or run `soniq inspect --cleanup` from a cron job.

### Worker logs say `LISTEN/NOTIFY setup failed, falling back to polling`

**Likely cause:** the database connection is going through PgBouncer in transaction-pooling mode. Transaction pooling does not preserve `LISTEN` registrations across statements.

**Fix:** either switch PgBouncer to session-pooling for the worker connection, or accept polling at `SONIQ_POLL_INTERVAL` (default 5 seconds) -- jobs will still run, they'll just have higher pickup latency.

## Recurring scheduler problems

### Recurring jobs are not firing

**Likely cause #1:** the scheduler is not running. The CLI worker (`soniq worker`) does not run the scheduler -- it's a separate process.

**Fix:**

```bash
SONIQ_JOBS_MODULES=app.jobs soniq scheduler
```

**Likely cause #2:** the scheduler is running but cannot acquire its advisory lock because PgBouncer is in transaction-pooling mode.

**Fix:** the scheduler uses a Postgres advisory lock to elect a single leader across replicas. Advisory locks are session-scoped. Transaction pooling breaks them. Switch to session pooling for the scheduler's connection (a direct Postgres connection is fine).

### Scheduler starts but immediately exits saying another instance is leader

This is expected. Multiple `soniq scheduler` processes can run for redundancy -- only one does work at a time. The runners-up wait, and one will take over if the leader dies.

## Enqueue problems

### `enqueue(..., connection=conn)` raises `ValueError`

**Likely cause:** transactional enqueue requires the PostgreSQL backend. SQLite and the in-memory backend do not support it.

**Fix:** use a `postgresql://...` URL, or remove `connection=conn` for non-Postgres environments.

### SQLAlchemy connection extraction fails with `AttributeError`

**Likely cause:** you're using the `psycopg3` async driver instead of `asyncpg`. The attribute chain `raw_conn.sync_connection.connection.driver_connection` only works with asyncpg.

**Fix:** switch the engine URL to `postgresql+asyncpg://...`. See the [SQLAlchemy section](guides/transactional-enqueue.md#pattern-3-sqlalchemy-async) of the transactional enqueue guide.

### `Task '...' is not registered locally` at enqueue time

**Likely cause:** the producer is calling `enqueue("name")` with a string but no job by that name has been registered in this process.

**Fix:** decide whether the producer should validate locally:

- If the producer also defines the job: import the module that registers it before enqueueing.
- If the producer is a pure producer with no local registry (cross-service setup): set `SONIQ_ENQUEUE_VALIDATION=warn` or `=none`, or call enqueue with a `TaskRef` instead of a string. See [cross-service jobs](guides/cross-service-jobs.md).

## Setup and migration problems

### `soniq setup` fails on a multi-replica deploy

**Likely cause:** `soniq setup` is being run from application startup. When multiple replicas start at once, they race to apply the same migrations.

**Fix:** run `soniq setup` only once per deploy, from a place where it cannot race with itself: a CI step before the deploy, a Kubernetes init container, a dedicated migration job, or an entrypoint script that runs only on the first replica.

### `soniq setup` succeeds but the worker still says it can't find tables

**Likely cause:** the worker's `SONIQ_DATABASE_URL` points to a different database than the one `soniq setup` wrote to.

**Fix:** print and compare the URLs. A common mistake is having `setup` use a localhost URL while the worker uses an internal Docker network URL pointing somewhere else.

## Dead-letter queue

### Dead-letter jobs are not appearing

**Likely cause:** failed jobs are still in retry. They only land in the dead-letter queue *after* exhausting `max_retries`.

**Fix:** wait for retries to complete, or check `soniq status --jobs` to see what state failures are in. Jobs in `dead_letter` status come from the `soniq_dead_letter_jobs` table, not `soniq_jobs`.

### `soniq dead-letter replay --all` re-enqueues thousands of jobs

If the underlying cause has not been fixed, those jobs will fail and end up back in the DLQ. The CLI prompts before replaying more than a handful of jobs; if you bypassed the prompt, the easiest mitigation is to delete the offending DLQ rows after fixing the cause.

## Pool and capacity

### Logs say `Connection pool too small: worker concurrency (X) plus reserved headroom (Y) requires Z`

**Likely cause:** the pool is undersized for the requested concurrency. Soniq reserves headroom for its LISTEN/NOTIFY connection and heartbeat writer.

**Fix:** raise `SONIQ_POOL_MAX_SIZE` so it is at least `concurrency + pool_headroom` (default headroom: 2). Or lower `--concurrency`.

### Worker "hangs" with high concurrency under load

**Likely cause:** sync handlers monopolising the bounded thread pool, blocking other jobs from claiming slots.

**Fix:** convert sync handlers to `async def`, or raise `SONIQ_SYNC_HANDLER_POOL_SIZE` (default 8).

## When to file an issue

Open a GitHub issue if the symptom isn't here, the suggested fix didn't work, or the suggested fix worked but the symptom recurs. Include:

- Soniq version (`soniq --version`)
- Postgres version (`SELECT version();`)
- The relevant config (env vars, CLI flags)
- Worker / scheduler logs around the failure (a few seconds before, a few seconds after)
