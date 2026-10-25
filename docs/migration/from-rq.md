---
description: Migrate from RQ (Redis Queue) to Soniq, a Python background job library that runs on PostgreSQL. Run RQ-style jobs without Redis, with a concept map and steps.
---

# Migrate from RQ to Soniq (Python jobs without Redis)

Soniq is a Python background job library that stores jobs in PostgreSQL, so it replaces the Redis that RQ needs. Moving from RQ means adding `@soniq.job(name=...)` to each job function, calling `await soniq.enqueue(func, arg=value)`, and running `soniq worker`. Soniq has no job dependencies (`depends_on`), no pickle, and no per-enqueue `Retry`. The concept map below covers each RQ feature, and the migration is six steps.

## Concept map

The fast version. Every row is expanded in the sections that follow.

| RQ | Soniq | Notes |
|---|---|---|
| `q = Queue(connection=Redis())` | `soniq = Soniq(database_url="postgresql://...")` | No Redis. Jobs live in Postgres. |
| `Queue("urgent")` | `queue="urgent"` argument on `@soniq.job(...)` or `enqueue(...)` | One app, many queue names. |
| Plain Python function (no decorator) | `@soniq.job(name="...")` decorator | Soniq registers by decorator; RQ resolves by import path at run time. |
| `q.enqueue(func, arg)` | `await soniq.enqueue(func, arg=arg)` | Async, keyword-only args. |
| `q.enqueue_in(timedelta(seconds=30), func, arg)` | `await soniq.enqueue(func, scheduled_at=timedelta(seconds=30), arg=arg)` | |
| `q.enqueue_at(datetime, func, arg)` | `await soniq.enqueue(func, scheduled_at=datetime, arg=arg)` | |
| `q.enqueue(func, retry=Retry(max=3, interval=[10, 30, 60]))` | `@soniq.job(retries=3, retry_delay=[10, 30, 60])` | Retries are a job-definition concern, not per-enqueue. |
| `job.get_status()` | `(await soniq.get_job(job_id))["status"]` | Status values differ - see "Job lifecycle". |
| `job.result` | `await soniq.get_result(job_id)` | |
| `job.cancel()` | `await soniq.cancel_job(job_id)` | |
| `q.empty()` | Not exposed for live jobs. Use SQL. For the dead-letter table, use `soniq dead-letter delete` or `soniq.dead_letter.bulk_delete(...)` | Mass-delete of queued jobs has no helper. |
| `rq worker` | `soniq worker` | |
| `rq worker queue1 queue2` | `soniq worker --queues queue1,queue2` | |
| `rq worker --burst` | `soniq worker --run-once` | Drains the ready jobs and exits. |
| `rq-scheduler` (separate package) | `soniq scheduler` (built in) + `@soniq.periodic(...)` | |
| RQ Dashboard / `rq info` | `soniq dashboard` and `soniq inspect` | Both built in. |
| `FailedJobRegistry` | `soniq_dead_letter_jobs` table; `soniq.dead_letter.replay(dlq_id)` | |
| `StartedJobRegistry` / `FinishedJobRegistry` | `soniq_jobs.status` | One table, four states, no per-state registry. |
| `get_current_job()` | A `ctx: JobContext` parameter on the handler | Has `job_id`, `attempt`, `queue` and more. |
| `depends_on=job` | Not supported | Enqueue the next job from the handler. |

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
soniq = Soniq(database_url=os.environ["DATABASE_URL"])
```

A URL string fits in env vars and config files, and most apps already have one for their ORM. The change usually looks like this:

```bash
# before
REDIS_URL=redis://localhost:6379

# after
DATABASE_URL=postgresql://localhost/myapp   # already there for your ORM
```

## Defining jobs: decorator vs no decorator

This is the biggest API difference between RQ and Soniq. Read it before you convert any code.

RQ jobs are just regular Python functions. The queue resolves them by import path at enqueue time:

```python
# myapp/jobs.py
def send_email(to, subject):
    ...

# producer
q.enqueue(send_email, "dev@example.com", subject="Hi")
# or, by import path:
q.enqueue("myapp.jobs.send_email", "dev@example.com", subject="Hi")
```

Soniq registers jobs explicitly via a decorator:

```python
# myapp/jobs.py
@soniq.job(name="emails.send_email")
async def send_email(to: str, subject: str):
    ...

# producer
await soniq.enqueue(send_email, to="dev@example.com", subject="Hi")
```

The decorator has two purposes:

- **Per-job configuration lives on the registration.** Retries, queue, priority, timeout and validation are decorator kwargs. RQ splits them between `Retry(...)` objects and `enqueue(...)` kwargs.
- **The worker imports the registration on startup.** The `name=` you pass is a stable identifier, and cross-service enqueue uses it. Always pass `name=`, because a name derived from the module path is deprecated and warns. RQ resolves the function by import path when the job runs, so renaming a module or moving a function breaks jobs that are already queued.

If your RQ jobs have no per-job options, add `@soniq.job(name="...")` above each function and change the body to `async def`.

## Argument style

RQ's `enqueue` is positional + arbitrary kwargs:

```python
q.enqueue(send_email, "dev@example.com", subject="Welcome")
```

Soniq's `enqueue` is keyword-only for job arguments. Pass the function as the first positional, then `arg=value`:

```python
await soniq.enqueue(send_email, to="dev@example.com", subject="Welcome")
```

The keyword-only style separates Soniq options (`queue`, `priority`, `scheduled_at`) from your job arguments. Soniq also rejects arguments that are not JSON (a `datetime`, a `UUID`) at enqueue time with `SONIQ_TASK_ARGS_INVALID`.

## Sync vs async handlers

RQ handlers are sync. Soniq prefers async and supports both:

```python
@soniq.job(name="jobs.fetch_async")
async def fetch_async(url: str):
    async with httpx.AsyncClient() as client:
        return (await client.get(url)).text

@soniq.job(name="jobs.fetch_sync")
def fetch_sync(url: str):
    return requests.get(url).text   # runs on a bounded thread pool
```

The thread pool is bounded: 8 threads per Soniq instance by default (`SONIQ_SYNC_HANDLER_POOL_SIZE`). When all threads are busy, sync jobs wait. You can keep sync handlers. Porting to `async def` is optional, and it helps most for I/O-bound handlers (HTTP, database queries, S3).

## Retries

RQ retries via a `Retry` object passed at enqueue time:

```python
# RQ
q.enqueue(fetch, url, retry=Retry(max=3, interval=[10, 30, 60]))
```

Soniq retries are a property of the job, set on the decorator:

```python
@soniq.job(name="jobs.fetch", retries=3, retry_delay=[10, 30, 60])
async def fetch(url: str):
    ...
```

Soniq has no per-enqueue retry override. For a different retry policy at one call site, define a second job with different decorator kwargs.

The default is `retries=3`. Any uncaught exception triggers a retry. To skip the retry, catch the exception in the handler and return normally.

## Job lifecycle and statuses

RQ tracks jobs through registries: `StartedJobRegistry`, `FinishedJobRegistry`, `FailedJobRegistry`, `ScheduledJobRegistry`, `DeferredJobRegistry`. Soniq has one `soniq_jobs` table with a `status` column that has four values:

| Soniq status | Closest RQ analog |
|---|---|
| `queued` | `queued` / `scheduled` / `deferred` (all the "waiting" registries collapse into one state) |
| `processing` | `started` |
| `done` | `finished` |
| `cancelled` | the result of `job.cancel()` before it ran |

There is no `failed` row state. A failure either re-queues the job (back to `queued`) or, after retries are exhausted, moves the job into the dedicated `soniq_dead_letter_jobs` table - which plays the role of RQ's `FailedJobRegistry`. The table is durable and queryable, and you can replay its rows:

```python
# Replay a dead-lettered job back into the queue:
await soniq.dead_letter.replay(dlq_id)
```

## Results

RQ stores `job.result` in Redis and exposes it as an attribute on the `Job` object. Soniq stores results in `soniq_jobs.result` (`JSONB`) and reads them back via `soniq.get_result(job_id)`:

```python
job_id = await soniq.enqueue(compute_total, order_id=123)
# ... later ...
result = await soniq.get_result(job_id)
```

Caveats:

- **Return values must be JSON-serialisable.** RQ uses pickle by default. Soniq is JSON-only. If a return value is not JSON, the job still completes and the stored result is `None`. Return dicts, lists and scalars, or store large outputs in S3 and return a reference.
- **No blocking `.result` accessor.** Poll with `soniq.get_result`. Soniq has no blocking helper, because waiting for a job turns a queue into an RPC.
- **Results expire.** Soniq deletes a completed job after `result_ttl` seconds. The default is 300 (5 minutes), and `SONIQ_RESULT_TTL` sets it. RQ keeps results for 500 seconds by default.

## Periodic jobs: rq-scheduler -> the scheduler

`rq-scheduler` is a separate package and process. Soniq includes a scheduler:

```python
from datetime import timedelta

@soniq.periodic(cron="0 * * * *", name="reports.hourly_rollup")
async def hourly_rollup():
    ...

@soniq.periodic(every=timedelta(minutes=15), name="cache.warm_caches")
async def warm_caches():
    ...
```

Run it with `soniq scheduler`. Replicas elect a leader with a Postgres advisory lock. One replica fires jobs and the others wait, so you get failover. Pass exactly one of `cron=` or `every=`.

## Workers: `rq worker` -> `soniq worker`

| RQ | Soniq |
|---|---|
| `rq worker --url redis://localhost` | `soniq worker app.jobs:soniq` (or `SONIQ_DATABASE_URL=postgresql://... SONIQ_JOBS_MODULES=app.jobs soniq worker`) |
| `rq worker high default low` | `soniq worker --queues high,default,low` |
| `rq worker --burst` | `soniq worker --run-once` |
| `rq info` | `soniq status` (queues and jobs) and `soniq inspect` (workers) |

Registrations happen at import time, so the worker must import your jobs modules. The `module:attribute` app path does this. Without an app path, set `SONIQ_JOBS_MODULES` or pass `--jobs-modules`.

## Things RQ has that Soniq does not

Check this list before you migrate:

- **Job dependencies (`depends_on`).** Soniq has no dependency graph. Enqueue the next job from the last step of the previous job.
- **Per-enqueue retry overrides via `Retry(...)`.** Soniq retries are set on the decorator. See "Retries".
- **Pickle serialisation.** Soniq is JSON-only.
- **Delivery guarantees.** RQ and Soniq do not guarantee exactly-once delivery. Soniq delivers at least once, so write idempotent handlers.
- **Burst mode with a different meaning.** `soniq worker --run-once` drains the ready jobs and exits. It does not wait for jobs that are scheduled for later.
- **Redis-only features.** Soniq runs on PostgreSQL only (plus SQLite and in-memory backends for tests).

## What you give up (and what you gain)

RQ has a small API. Soniq is not smaller. It removes Redis from your stack. If you do not already run Postgres, you swap one service for another, and the move has little value. The case for Soniq is strongest when:

- You already run Postgres for application data.
- You want transactional enqueue, so a job commits or rolls back with your database writes.
- You want async-native workers.
- You prefer one monitored database over Redis plus Postgres.

If none of these apply, stay on RQ.

## Migration sequence

RQ and Soniq share no state, so you can move one module at a time.

1. Install Soniq alongside RQ.
2. Run `soniq setup app.jobs:soniq` against your Postgres database.
3. Convert one job module. Add `@soniq.job(name="...")` to each function. Change sync functions to `async def` if you can. Replace `q.enqueue(...)` with `await soniq.enqueue(...)`.
4. Run a Soniq worker: `soniq worker app.jobs:soniq`.
5. Stop enqueuing into Redis. Wait for the RQ queue to empty. Stop the RQ workers.
6. Remove RQ from `pyproject.toml`. Remove Redis if nothing else uses it.

The API differs from RQ in four ways: the decorator is required, arguments are keyword-only, handlers are async, and results are JSON-only. The rewrite is mechanical.

## See also

- [Quickstart](../quickstart.md) - five minutes from `pip install` to first job
- [Migrate from Celery](from-celery.md) - the same guide for Celery users
- [Transactional enqueue](../guides/transactional-enqueue.md) - enqueue a job in the same database transaction as your data, which a Redis-backed queue cannot do
- [Tutorial: defining jobs](../tutorial/01-defining-jobs.md) - the decorator API in depth
- [Going to production](../production/going-to-production.md)
