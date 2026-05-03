# Migrate from RQ

This guide is for teams running RQ (Redis Queue) who want to move to Soniq. RQ is intentionally minimal - the entire surface fits in one head - so this migration is short and mostly mechanical. Most of the page below is the concept map; the migration sequence at the end is six steps.

## Concept map

The fast version. Every row is expanded in the sections that follow.

| RQ | Soniq | Notes |
|---|---|---|
| `q = Queue(connection=Redis())` | `app = Soniq(database_url="postgresql://...")` | No Redis. Jobs live in Postgres. |
| `Queue("urgent")` | `queue="urgent"` argument on `@app.job(...)` or `enqueue(...)` | One app, many queue names. |
| Plain Python function (no decorator) | `@app.job()` decorator | Soniq registers by decorator; RQ resolves by import path at run time. |
| `q.enqueue(func, arg)` | `await app.enqueue(func, arg=arg)` | Async, keyword-only args. |
| `q.enqueue_in(timedelta(seconds=30), func, arg)` | `await app.enqueue(func, delay=30, arg=arg)` | |
| `q.enqueue_at(datetime, func, arg)` | `await app.enqueue(func, scheduled_at=datetime, arg=arg)` | |
| `q.enqueue(func, retry=Retry(max=3, interval=[10, 30, 60]))` | `@app.job(retries=3, retry_delay=[10, 30, 60])` | Retries are a job-definition concern, not per-enqueue. |
| `job.get_status()` | `(await app.get_job(job_id))["status"]` | Status values differ - see "Job lifecycle". |
| `job.result` | `await app.get_result(job_id)` | |
| `job.cancel()` | `await app.cancel_job(job_id)` | |
| `q.empty()` | Not exposed; use SQL or `app.dead_letter.purge(...)` for the DLQ | Mass-delete is intentionally not a one-liner. |
| `rq worker` | `soniq worker` | |
| `rq worker queue1 queue2` | `soniq worker --queues queue1,queue2` | |
| `rq worker --burst` | not exposed today | Soniq workers are long-lived. |
| `rq-scheduler` (separate package) | `soniq scheduler` (built in) + `@app.periodic(...)` | |
| RQ Dashboard / `rq info` | `soniq dashboard` and `soniq inspect` | Both built in. |
| `FailedJobRegistry` | `soniq_dead_letter_jobs` table; `app.dead_letter.replay(dlq_id)` | |
| `StartedJobRegistry` / `FinishedJobRegistry` | `soniq_jobs.status` | One table, four states - no per-state registry. |

## Connection vs URL

RQ takes a Redis connection object:

```python
from redis import Redis
from rq import Queue
q = Queue(connection=Redis(host="localhost", port=6379))
```

Soniq takes a `database_url` string:

```python
from soniq import Soniq
app = Soniq(database_url=os.environ["DATABASE_URL"])
```

The string form serialises cleanly into env vars and config files, matches what every other Python database library expects, and means most apps already have it sitting in `.env`. The migration usually looks like:

```bash
# before
REDIS_URL=redis://localhost:6379

# after
DATABASE_URL=postgresql://localhost/myapp   # already there for your ORM
```

## Defining jobs: decorator vs no decorator

This is RQ's biggest API difference, and it's worth understanding before you convert any code.

RQ jobs are just regular Python functions. The queue resolves them by import path at enqueue time:

```python
# myapp/jobs.py
def send_email(to, subject):
    ...

# producer
q.enqueue(send_email, "dev@example.com", subject="Hi")
# or:
q.enqueue("myapp.jobs.send_email", "dev@example.com", subject="Hi")
```

Soniq registers jobs explicitly via a decorator:

```python
# myapp/jobs.py
@app.job()
async def send_email(to: str, subject: str):
    ...

# producer
await app.enqueue(send_email, to="dev@example.com", subject="Hi")
```

Why the decorator? Two reasons:

- **Per-job configuration lives on the registration.** Retries, queue, priority, timeout, validation - all decorator kwargs. RQ scatters these across `Retry(...)` objects and `enqueue(...)` kwargs.
- **The worker imports the registration on startup.** That gives Soniq a stable task name (`module.qualname` by default, or whatever you pass to `name=`) which is the wire-protocol identifier for cross-service enqueue. RQ resolves the function lazily at job-execution time, which is flexible but means refactoring (renaming a module, moving a function) silently breaks any in-flight job.

If your RQ jobs don't have any per-job options today, the migration is just adding `@app.job()` above each function and changing the body to `async def`. About as mechanical as renames get.

## Argument style

RQ's `enqueue` is positional + arbitrary kwargs:

```python
q.enqueue(send_email, "dev@example.com", subject="Welcome")
```

Soniq's `enqueue` is keyword-only for job arguments. Pass the function as the first positional, then `arg=value`:

```python
await app.enqueue(send_email, to="dev@example.com", subject="Welcome")
```

The keyword-only style means there's never ambiguity between framework options (`queue`, `delay`, `priority`, `scheduled_at`) and your job's own arguments. RQ avoids the same ambiguity by namespacing options under `Retry(...)` etc., which is a different solution to the same problem.

## Sync vs async handlers

RQ handlers are sync. Soniq prefers async, but supports both:

```python
@app.job()
async def fetch_async(url: str):
    async with httpx.AsyncClient() as client:
        return (await client.get(url)).text

@app.job()
def fetch_sync(url: str):
    return requests.get(url).text   # runs on a bounded thread pool
```

The thread pool is per-worker and bounded - if all threads are busy, sync jobs queue up rather than oversubscribing the host. You can keep sync handlers indefinitely; the recommendation to port to `async def` is a performance tip, not a requirement. If your handlers do I/O (HTTP, database queries, S3), the async port usually pays for itself.

## Retries

RQ retries via a `Retry` object passed at enqueue time:

```python
# RQ
q.enqueue(fetch, url, retry=Retry(max=3, interval=[10, 30, 60]))
```

Soniq retries are a property of the job, set on the decorator:

```python
@app.job(retries=3, retry_delay=[10, 30, 60])
async def fetch(url: str):
    ...
```

There's no per-enqueue override today - if you need a one-off retry policy for a specific call site, the recommended pattern is to define a second job with different decorator kwargs. We've yet to see a real use case where this is awkward.

By default, any uncaught exception triggers a retry. To suppress, catch it in the handler and return normally.

## Job lifecycle and statuses

RQ tracks jobs through registries: `StartedJobRegistry`, `FinishedJobRegistry`, `FailedJobRegistry`, `ScheduledJobRegistry`, `DeferredJobRegistry`. Soniq has one `soniq_jobs` table with a `status` column pinned to four values:

| Soniq status | Closest RQ analog |
|---|---|
| `queued` | `queued` / `scheduled` / `deferred` (all the "waiting" registries collapse into one state) |
| `processing` | `started` |
| `done` | `finished` |
| `cancelled` | the result of `job.cancel()` before it ran |

There is no `failed` row state. A failure either re-queues the job (back to `queued`) or, after retries are exhausted, moves the job into the dedicated `soniq_dead_letter_jobs` table - which plays the same role as RQ's `FailedJobRegistry` but is a queryable, replayable durable record:

```python
# Replay a dead-lettered job back into the queue:
await app.dead_letter.replay(dlq_id)
```

## Results

RQ stores `job.result` in Redis and exposes it as an attribute on the `Job` object. Soniq stores results in `soniq_jobs.result` (`JSONB`) and reads them back via `app.get_result(job_id)`:

```python
job_id = await app.enqueue(compute_total, order_id=123)
# ... later ...
result = await app.get_result(job_id)
```

Caveats:

- **Return values must be JSON-serialisable.** RQ uses pickle by default; Soniq is JSON-only on purpose. Return dicts/lists/scalars, or persist large outputs to S3 and return a reference.
- **No blocking `.result` accessor.** Async polling is your tool. We deliberately don't ship a blocking helper because most "wait for this job" patterns turn the queue into an RPC.
- **Results live as long as the job row.** Configure `SONIQ_DONE_JOB_RETENTION_DAYS` if you produce a lot of jobs.

## Periodic jobs: rq-scheduler -> the scheduler

`rq-scheduler` is a separate package and a separate process. Soniq ships an equivalent built in:

```python
from datetime import timedelta

@app.periodic(cron="0 * * * *")
async def hourly_rollup():
    ...

@app.periodic(every=timedelta(minutes=15))
async def warm_caches():
    ...
```

Run it with `soniq scheduler`. Multiple replicas coordinate via a Postgres advisory lock - one is leader, the rest idle, so you get failover for free.

## Workers: `rq worker` -> `soniq worker`

| RQ | Soniq |
|---|---|
| `rq worker --url redis://localhost` | `SONIQ_DATABASE_URL=postgresql://... SONIQ_JOBS_MODULES=app.jobs soniq worker` |
| `rq worker high default low` | `soniq worker --queues high,default,low` |
| `rq worker --burst` | not supported today - workers are long-lived |
| `rq info` | `soniq inspect` |

`SONIQ_JOBS_MODULES` is the env-var equivalent of "tell the worker which modules to import" - registrations happen at import time, so the worker needs to import your jobs modules to know what tasks exist. Either set the env var or pass `--jobs-modules`.

## Things RQ has that Soniq does not

Worth flagging before the migration:

- **`--burst` mode.** RQ workers can drain the queue and exit. Soniq workers are long-lived. If you used `--burst` for cron-triggered batch processing, the equivalent is "run a one-off Python script that calls `app.enqueue(...)` and then exits", and let normal workers process the result.
- **Per-enqueue retry overrides via `Retry(...)`.** Soniq retries are decorator-level; see "Retries" above.
- **Pickle serialisation.** Soniq is JSON-only.

## What you give up (and what you gain)

RQ already runs without an extra service in some setups (if you happen to have Redis), and its API is famously small. Soniq doesn't try to be smaller - it tries to remove Redis from your stack. If you don't already run Postgres, you're trading one service for another, which is not a win. The migration argument is strongest when:

- You already run Postgres for application data
- You want transactional enqueue (atomic with your DB writes)
- You want async-native workers
- You'd rather have one well-monitored Postgres than one Redis + one Postgres

If those don't apply, RQ is fine. Stay on it.

## Migration sequence

Because RQ's surface is small, the recommended path is direct rather than a long side-by-side period:

1. **Install Soniq** alongside RQ.
2. **Run `soniq setup`** against your Postgres database.
3. **Convert one job module.** Add `@app.job()` to each function, switch sync to `async def` if you can, replace `q.enqueue(...)` calls with `await app.enqueue(...)`.
4. **Run a Soniq worker:** `SONIQ_JOBS_MODULES=app.jobs soniq worker`.
5. **Drain the RQ queue.** Stop enqueuing into Redis. Wait for the queue to empty. Stop RQ workers.
6. **Remove RQ** from `pyproject.toml` and the Redis dependency if nothing else uses it.

The API surface is different enough (decorator-required, keyword-only args, sync->async, JSON-only results) that the rewrite is direct rather than aliased. The good news is that it is mechanical and one module at a time.

## See also

- [Quickstart](../quickstart.md) - five minutes from `pip install` to first job
- [Transactional enqueue](../guides/transactional-enqueue.md) - the killer feature Redis-backed queues cannot offer
- [Tutorial: defining jobs](../tutorial/01-defining-jobs.md) - the decorator API in depth
- [Going to production](../production/going-to-production.md)
