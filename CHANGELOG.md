# Changelog

All notable changes to Soniq are documented in this file.

The format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-11-01

The first step towards rows that any language can read and write: code in any language can now enqueue a job with one SQL call and read its result back. See the new [Storage format](docs/reference/storage-format.md) page.

### Upgrading from 0.1.0

Run `soniq setup` once to apply migration `0006_sql_api` (or export it with `soniq migrations export --from 0005`). It only adds functions, changes a column default and fixes old dead-letter tags, so it is safe while 0.1.0 workers run. Rows written by 0.1.0 are still processed, and 0.1.0 workers can run rows written by 0.2.0, so a rolling deploy or a rollback is safe. The full steps are in [Upgrading from 0.1.0](docs/production/upgrading.md). Some behavior changes may need a look:

- A handler that returns something that isn't JSON (a `datetime`, a Pydantic model) used to have its `str()` stored as the result. Now the job still completes, the worker logs an error, and the stored result is `None`.
- `enqueue` rejects `NaN`, `Infinity` and `-Infinity` with `SONIQ_TASK_ARGS_INVALID`. They used to get past the check and fail inside the database driver.
- `@soniq.job()` and `@soniq.periodic(...)` without `name=` warn. See Deprecations.
- During a rolling deploy, a handler that returns something that isn't JSON gets its `str()` stored on a 0.1.0 worker and `None` on a 0.2.0 one.
- `enqueue` also rejects args holding a NUL character (`\x00`) or a lone surrogate, which Postgres can't store. They used to fail with a raw driver error.
- The dashboard API key must come in the `X-API-Key` header. `?api_key=` is no longer accepted, because query strings end up in access logs. To share a link, use `#api_key=<key>`: the page saves it and clears it from the address bar.
- If you applied migration 0006 from a release candidate, apply it again: `soniq migrations export --sql --from 0005 | psql -v ON_ERROR_STOP=1 "$DATABASE_URL"`. The rc version of its functions let any role that can create objects in `public` run code as the function owner (see Security). Running it twice is safe.
- Unique-job hashes changed for args with floats that print with an exponent (`1e16`, `1e-07`), for `-0.0`, and for dicts with int keys when the order changes as text (ten or more keys, say). They now match the hash SQL computes from the stored args. A unique job queued by 0.1.0 with such args isn't seen as a duplicate of the same job enqueued by 0.2.0.
- A dashboard running without an API key now answers only requests addressed to `localhost`, `127.0.0.1` or `[::1]`, and gives anything else a 400. If you serve a keyless dashboard under a real hostname, list it in `SONIQ_DASHBOARD_ALLOWED_HOSTS` (comma-separated), or better, set an API key.
- Webhook delivery no longer follows redirects. A 3xx response counts as a failed delivery.
- Unique-job args nested 500 or more levels deep are rejected with `SONIQ_TASK_ARGS_INVALID` by `enqueue`. `soniq_enqueue()` rejects that depth for every job.
- Soniq now supports PostgreSQL 15 and newer, and CI runs 15 and 18. On 14 or older `soniq setup` logs a warning but still runs. PostgreSQL 14 reaches community end of life on 2026-11-12.
- On a database created before Postgres 15, including one moved to a newer version with `pg_upgrade`, run `REVOKE CREATE ON SCHEMA public FROM PUBLIC` (see Security and [Going to production](docs/production/going-to-production.md)).
- `get_job` and the dashboard return args or a result that Python can't decode as the raw JSON text, instead of failing.
- `enqueue(connection=...)` with a raw asyncpg connection that isn't in a transaction raises `SONIQ_UNSUPPORTED_CONNECTION`, as an SQLAlchemy session without one already did. Before, the job was committed at once, outside any transaction you meant it to join. Wrap the call in `async with conn.transaction():`.
- Backend protocol: `mark_job_done`, `mark_job_failed`, `mark_job_dead_letter` and `reschedule_job` take a `claimed_attempts` keyword and return whether the row was written. The built-in backends are updated. A custom backend must accept the new keyword.
- Minimum versions are now `aiohttp>=3.10.11` and `cryptography>=42.0.4`.
- The dashboard API changed: new endpoints, `limit`/`offset`/`hours` are range-checked (bad values get a 422), an unknown panel is a 404, metrics count only finished jobs, and timestamps are UTC with an offset. Rolling the dashboard back removes the new endpoints.

### Security

- **`soniq_enqueue()` could run another role's code as its owner** (fixed before release, present in 0.2.0rc1 to rc3). On Postgres 14 and older, any role can create functions in `public`, and a planted `public.convert_to(text, text)` or a custom operator beats the built-in on argument types even with `search_path` pinned. The SQL functions now call every built-in and operator through `pg_catalog`, and `pg_temp` is last on their `search_path`. On a database created before Postgres 15, also run `REVOKE CREATE ON SCHEMA public FROM PUBLIC`.
- **Soniq's own queries could run code planted in `public`** (present since 0.1.0). On Postgres 14 and older, any role that can log in can create functions in `public`. A planted `public.pg_advisory_lock(float8)` beat the built-in, so `soniq setup` run as a superuser handed the planter superuser. Workers, the scheduler, the dashboard and log queries had the same problem with other built-ins. Every built-in Soniq calls with a parameter is now qualified with `pg_catalog` or cast to its exact type. Revoke `CREATE` on `public` anyway.
- **One row with args Python can't decode stopped every worker.** Postgres stores ints over 4300 digits and nesting deeper than Python's recursion limit, and decoding them failed the claim query on every poll, so nothing else ran. Such a job is now dead-lettered as `invalid_arguments`, with its args kept as they were, and the workers move on. `soniq_enqueue()` rejects args nested 500 or more levels deep for every job, not only unique ones.
- **A dashboard with no API key could be read through DNS rebinding.** A web page the operator visited could point its own hostname at `127.0.0.1` and read jobs, args and results. It now checks the `Host` header (see Upgrading).
- **Webhooks followed redirects**, so a 307 or 308 re-sent the signed body and any secret headers to another address, including internal ones, and stored the response.
- **The CLI banner could print a database password** that held `/`, `#`, `?`, `[` or `@`, and never masked `sslpassword=`. Passwords are now masked on the raw string, and an address that can't be read prints as `<unparseable url>`.
- **`soniq dead-letter export` created files anyone on the machine could read and followed symlinks.** New files are created with mode 0600, and a symlink at the output path is refused.
- **The dashboard page still took the key from `?api_key=`**, which ends up in access logs. Only `#api_key=` works now, as documented.
- **The dashboard sent no security headers**, so another site could frame it. Every response now carries `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer` and a Content Security Policy. The page no longer loads Google Fonts, and every dashboard query runs under a 15 second `statement_timeout`, so a slow search can't hold pool connections.
- **Webhook signatures could be replayed**, because the timestamp wasn't signed. Deliveries now also send `X-Webhook-Signature-V2`, an HMAC over `<timestamp>.<body>`. `X-Webhook-Signature` is unchanged. Receivers should check V2 and reject old timestamps (see [Webhook delivery](docs/recipes/webhook-delivery.md)).
- **`validate=` errors echoed argument values** into the error and the stored job error. They now name the field and the problem only.
- `repr(SoniqSettings)` no longer shows the database password. Without `SONIQ_SECRET_KEY`, the warning now says webhook secrets won't decrypt after a restart, and a failed decrypt logs why. `is_encrypted` checks the real token layout instead of a length, so a long plain secret is no longer taken for an encrypted one.
- `soniq dashboard` warns at startup when `SONIQ_DASHBOARD_WRITE_ENABLED` is set without `SONIQ_DASHBOARD_API_KEY`, instead of quietly starting read-only.
- The storage format page has a new Trust model section: dedup keys, unique hashes and job ids are one namespace shared by every role granted `soniq_enqueue`, `soniq_get_job` shows any job to any grantee, and `producer_id` is whatever the client sets as `application_name`.

### Deprecations

- **Job names derived from the module path.** `@soniq.job()` or `@soniq.periodic(...)` with no `name=` still registers as `module.function`, but warns and goes away in 1.0. Set `name=` to the name the warning prints, so jobs already queued under it still find their handler. The warning also goes to the `soniq` logger at WARNING, so it shows up in worker logs. With `SONIQ_DEPRECATIONS=error` it fails at import, as the decorator runs.
- **`soniq dashboard --reload`.** It never did anything. It still parses, warns, and goes away in 1.0.

### Changed

- Soniq writes plain JSON only for job args and results. Unknown objects are no longer turned into strings, and `NaN` is rejected instead of written. JSONB that your own code writes through `soniq.backend.acquire()` is encoded as in 0.1.0.
- The `soniq_jobs.max_attempts` column default is 4, matching what Python writes (`retries=3` plus the first attempt). It only matters for raw `INSERT`s that leave the column out.
- The docs and examples pass `name=` to every job.

### Fixed

- The example configs in `deployment/` work again. They used commands and settings that don't exist (`soniq health`, `soniq ready`, `SONIQ_SCHEDULING_ENABLED`, `SONIQ_DASHBOARD_ENABLED`), a dashboard port of 8000, and a stale `fastjob` database URL. The systemd worker unit had a 60-second watchdog that killed the worker, and `supervisor.conf` had a broken `[group:soniq]` header and no `SONIQ_JOBS_MODULES`. Compose and Kubernetes now run `soniq setup` before the workers, and the dashboard uses port 6161 with an API key.
- The `SONIQ_POOL_TOO_SMALL` error told you to lower `SONIQ_CONCURRENCY`, which nothing reads. It now names `--concurrency`.
- A worker that stalled past the heartbeat timeout (a sync handler blocking the event loop, say) could finish after the sweep had handed its job to another worker, and overwrite, requeue or dead-letter the job while the second worker ran it. A worker now only writes the outcome of its own claim. A late outcome is dropped with a warning, `after_job` hooks don't run for it, and metrics sinks get `record_job_end(status="reclaimed")`.
- Dead-letter listings no longer fail on a row whose `tags` Python can't decode. Those tags read as `None`.
- A `ctx: JobContext` parameter now works in modules that use `from __future__ import annotations`. Before, the annotation was a string, so the worker never passed the context and `enqueue` rejected the job with "missing a required argument: 'ctx'".
- Dead-letter `tags` passed to the Postgres backend were encoded twice and stored as a JSON string instead of an object. Migration 0006 fixes rows already stored that way, and reads accept both shapes.
- Workers accept a job row whose `max_attempts` is NULL and use the handler's `retries=` for it, and such a job can be dead-lettered. Before, the worker raised `TypeError` and left the job stuck in `processing`. Soniq doesn't write NULL yet, and workers older than 0.2.0 still get stuck on it, so don't insert NULL yourself.
- Enqueuing a job that is both unique and has a `dedup_key` raised a unique-violation error when the duplicate matched on the other key. It now returns the existing job's id, on asyncpg and psycopg.
- `enqueue` sent two wake-up notifications per job on Postgres. It sends one.
- The dashboard API key check now works when the dashboard is mounted under a path, and CORS preflight requests no longer need the key. Write buttons show only when both write mode and an API key are set.
- Dashboard job details and job types include dead-lettered jobs, and the error rate is dead-lettered jobs over finished jobs in the last hour.
- `soniq dashboard` without the dashboard extra prints only the install hint.
- The wheel ships the migrations README, and the package lists Python 3.14.
- Dashboard metrics counted queued and cancelled jobs as processed and left dead-lettered jobs out. Now only finished jobs count, and failures are the jobs that reached the dead-letter table. Job types use the same rule.
- The dashboard chart counted completions in the hour a job was enqueued and dropped hours with only failures. It now buckets each event by when it happened, in whole UTC hours.
- The dashboard works when mounted under a path, sends the API key it was given, and escapes job names and errors it shows.
- A job whose error message held a NUL character, or whose result held a lone surrogate, stayed in `processing` forever, because the final database write failed. The error text drops those characters, and such a result is stored as `None`.
- The stale-worker sweep goes by heartbeat age alone. It requeues a `processing` job when its worker row is gone or its last heartbeat is older than the timeout, whatever the row's status, so a worker stopped with a job still processing is caught too. A worker that was marked stale while its event loop stalled, or a 0.1.0 worker swept during a rolling deploy, keeps its jobs as long as it heartbeats, and its next heartbeat marks it active again.
- A worker restarted with the same hostname and pid (a container restarted in place, as pid 1) couldn't register while any job row still pointed at the old worker. Once the old row's heartbeat is stale, the new worker replaces it and requeues anything it held. If a live worker holds that hostname and pid (two host-network containers can both be pid 1), the new one registers as `hostname#<first 8 chars of its id>` and leaves the live one alone.
- A worker whose row was deleted registers again on its next heartbeat. Before, every claim after that failed on the `soniq_jobs` foreign key.
- The heartbeat kept quiet during a graceful shutdown, so a drain longer than the heartbeat timeout let another worker run the same job again. It now runs until in-flight jobs finish.
- `enqueue(dedup_key="")` raised a raw unique-violation error on Postgres the second time, and created duplicates on SQLite and in memory. An empty key now dedups like any other.
- A job stayed in `processing` forever when its result couldn't be stored (nested too deep to encode, or over Postgres's size limit for one JSON string) or when its exception's `str()` raised. The job now completes with result `None`, or fails with `<exception str() failed>` as the error.
- A `TimeoutError` raised by the handler itself was reported as "Job timed out after 300.0s". Only the job's own timeout gets that message now.
- `ctx: Optional[JobContext]` and `ctx: JobContext | None` now get the context. Before, the handler got `None`, or `enqueue` rejected the job when there was no default.
- On SQLite, concurrent unique or dedup enqueues from one process could all insert, and concurrent claims could hand one job to several worker tasks. Writes now go through a lock. It works inside one process only, so don't point two processes at one SQLite file.
- The memory backend (`soniq_memory`) gave handlers tuples, `Enum` objects and int dict keys where Postgres gives lists, plain values and string keys. It now stores args the way they come back from JSON, and the `ctx` argument no longer leaks into the stored args.
- A unique job whose args had both int and str dict keys raised a raw `TypeError`. The hash is now taken over the args as they are stored, so `{1: "a"}` and `{"1": "a"}` are the same job.
- Dashboard: the Queued, Running, Done and Cancelled tiles on the overview now open the filtered job list, and a `#/failed&api_key=...` share link keeps its route. The pager no longer vanishes on a later page, and the drawer no longer offers Delete for a job that was just dead-lettered.
- Dashboard: the API key is read once at startup and used for the auth check, the write guard and the page. Before, a key that reached the environment after the app was built switched on writes without the check.
- Dashboard: `DELETE /api/jobs/{id}` refuses a running job with a 409, and a missing job is a 404, as is replaying a missing dead-letter id. `/api/system/health` reports the database as down instead of returning a 500, a NUL in a filter is a 422, search treats `%`, `_` and `\` literally, the chart no longer counts replayed dead-letters, `/api/workers/stats` `active_jobs` counts running jobs only, queues that exist only in the dead-letter table sort with the rest, and `/api/dead-letter` returns `tags` as an object for old rows too.
- `soniq dead-letter export --format csv` crashed on an empty dead-letter table. It writes the header row. Export now writes straight to `--output`, so a missing directory or a read-only working directory gives a clear error instead of a stray temp file.
- The CLI's "Using instance-based configuration" line goes to stderr, so piping `dead-letter list` or `export` gives clean output.
- `soniq dead-letter list`, `cleanup` and `export` accept the app as a positional argument, like the other commands, and `replay` and `delete` name the id that isn't a UUID.
- Running `soniq setup` from several replicas at once on an empty database could fail on a duplicate type or table. Setup now takes its lock before creating the migrations table.
- `soniq_enqueue(priority => NULL)` wrote a job that couldn't be dead-lettered, so it got stuck after its last attempt.
- The deprecation warning for a derived job name could suggest a name that `task_name_pattern` rejects (an uppercase letter or a leading underscore in the module path). It now says so and what to do about it.
- Migration 0006 waits at most 5 seconds for its table lock and takes the exclusive lock last, so it can't stall the queue behind it.
- `soniq_enqueue()` ignores a `soniq.task_name_pattern` set by the caller's session or role. Only the database-level setting counts.
- `soniq_args_hash()` and its helpers work when the Soniq schema isn't on the caller's `search_path`.
- Dashboard metrics and health counted a replayed dead-letter job twice: once as the failure and again as the replay. The error rate no longer counts dead-letter rows that were replayed.
- The dashboard compares API keys in constant time, and a huge `offset` gets a 422 instead of a 500.
- `soniq migrations export --alembic` broke under Alembic's offline `--sql` mode, which doubles `%` signs. The migrations no longer contain any.
- `soniq migrations export --sql` wraps its output in `BEGIN` and `COMMIT`, so piping it to `psql` applies it as one transaction and its lock timeout takes effect.
- Python and SQL computed different unique-job hashes for some floats and for int dict keys, so a job enqueued from SQL didn't dedup against the same job from Python.
- `soniq_enqueue()` rejects numbers too big for a double (they reached Python handlers as `inf`) and `max_attempts` below 1 with a hint. `max_attempts => NULL` stores NULL, so the worker uses the handler's `retries=`.
- Install hints quote the extra (`pip install "soniq[dashboard]"`), so they work in zsh.
- The testing guide's dead-letter example used the memory fixture and `max_retries=`. It uses `soniq_pg` and `retries=`.
- Concurrent first calls on a fresh instance (50 enqueues in a `gather`, say) each opened their own connection pool, so a process could hold 250 connections with `pool_max_size=5`. They now share one pool. 0.1.0 had the same bug.
- Enqueueing before `soniq setup` raises `SoniqError` with code `SONIQ_SCHEMA_MISSING` and says to run setup, instead of a raw "relation soniq_jobs does not exist".
- A retry or snooze due before the next poll waited for that poll, so `retry_delay=1` ran after `poll_interval` (5 seconds by default). The worker now wakes when its short retries come due.
- Webhook retries sent the payload with its keys reordered by JSONB, so the body differed from the first attempt. Every attempt sends the same bytes now, with sorted keys.
- Dashboard: the Host-check 400 and API-key 401 responses lacked the security headers, and the page's CSP blocked FastAPI's `/docs` and `/redoc` pages. Both now work.
- `import soniq` no longer imports FastAPI, which saves about 150 ms in processes that never start the dashboard.
- `soniq status --jobs` shows full job names instead of cutting them at 25 characters.
- Webhook and log statistics bucket by hour in UTC, whatever the session time zone is.
- The docs' YAML front matter parses again in seven pages, and the storage-format and upgrade pages warn that 0.1.0 workers can't run a row with `max_attempts` set to NULL.

### Added

- **SQL enqueue.** `soniq_enqueue()` enqueues a job from any language with the same name and args checks, unique-job dedup and worker wake-up as Python. `soniq_get_job()` reads its status and result, dead-lettered jobs included. Both are granted per role. `soniq_args_hash()` computes the unique-job hash in SQL.
- **Storage format page** with the SQL functions, the tables, the JSON rules and the exact `args_hash` algorithm, including a test vector.
- **Upgrade guide** from 0.1.0.
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
