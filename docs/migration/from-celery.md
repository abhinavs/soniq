---
description: Migrate from Celery to Soniq, a Python background job library that uses PostgreSQL instead of Redis or RabbitMQ. Concept map, code changes, gaps.
---

# Migrate from Celery to Soniq (Python jobs on PostgreSQL)

Soniq is a Celery alternative for Python teams that already run PostgreSQL. It replaces the Redis or RabbitMQ broker and the result backend with one Postgres database, and its workers run on `asyncio`. Most Celery tasks move over by changing the decorator, the enqueue call and the worker command. Soniq has no chains, chords, groups, pickle serialisation or `bind=True`, so check the list near the end of this page first.

Treat the migration as a rewrite at the call sites: `task.delay(arg)` becomes `await soniq.enqueue(task, arg=arg)`, and `@app.task` becomes `@soniq.job(name=...)`. You can move one module at a time, because Celery and Soniq share no state.

## Concept map

The fast version. Every row is expanded in the sections that follow.

| Celery | Soniq | Notes |
|---|---|---|
| `Celery("app", broker="redis://...")` | `Soniq(database_url="postgresql://...")` | No broker. Jobs live in your Postgres. |
| `@app.task` | `@soniq.job(name="...")` | Always called with parentheses. Always pass `name=`. |
| `@app.task(bind=True, max_retries=5)` | `@soniq.job(retries=5)` | No `bind=True` - use `JobContext` or hooks. See "Hooks and JobContext instead of `bind=True`". |
| `task.delay(arg)` | `await soniq.enqueue(task, arg=arg)` | Always `await`ed. Keyword-only args. |
| `task.apply_async(args=[...], kwargs={...}, countdown=30)` | `await soniq.enqueue(task, ..., scheduled_at=30)` | A number is seconds from now. |
| `task.apply_async(eta=datetime)` | `await soniq.enqueue(task, scheduled_at=datetime, ...)` | |
| `task.apply_async(queue="urgent")` | `await soniq.enqueue(task, queue="urgent", ...)` | Or set `queue=` on `@soniq.job(...)` for a default. |
| `result = task.delay(...); result.get()` | `result = await soniq.get_result(job_id)` | See "Results and the result backend". |
| `result.status` | `(await soniq.get_job(job_id))["status"]` | Status values differ - see "Job lifecycle". |
| `app.control.revoke(task_id)` | `await soniq.cancel_job(job_id)` | |
| `celery -A app worker -Q urgent,default` | `soniq worker --queues urgent,default` | |
| `celery -A app beat` | `soniq scheduler` + `@soniq.periodic(...)` | Beat replaced by a leader-elected scheduler service. |
| `celery -A app inspect active` | `soniq inspect` | |
| Flower | `soniq dashboard` | Built in, runs on port 6161 by default. |
| `chain(a.s(), b.s(), c.s())` | Not supported. Enqueue the next job from the previous one's body. | |
| `chord(group, callback)` | Not supported. Use a counter row and a finaliser job. | |
| `@app.task(autoretry_for=(IOError,), retry_backoff=True)` | `@soniq.job(retries=N, retry_backoff=True, retry_delay=N)` | Soniq retries on every uncaught exception (default `retries=3`). Catch an exception in the handler to skip the retry. |

## App and broker: there is no broker

Celery needs a broker. Soniq has none. Your Postgres database is the queue.

```python
# before
from celery import Celery
app = Celery("myapp", broker="redis://localhost:6379/0", backend="redis://localhost:6379/1")

# after
from soniq import Soniq
soniq = Soniq(database_url=os.environ["DATABASE_URL"])
```

Note two differences:

- **No separate result backend.** Soniq stores results in the same `soniq_jobs` row as the job itself (in a `result` JSONB column). If you previously relied on a separate Redis instance for `backend=`, that infrastructure goes away.
- **The `database_url` is almost certainly already in your `.env`** for your ORM. The migration usually deletes `BROKER_URL` and `RESULT_BACKEND` rather than adding anything.

## Defining tasks: `@app.task` -> `@soniq.job(...)`

The decorator is the most visible diff. Soniq always uses parentheses, even with no kwargs.

```python
# before
@app.task
def send_email(to, subject):
    ...

@app.task(bind=True, max_retries=5, queue="urgent")
def process_payment(self, order_id):
    try:
        ...
    except IOError as exc:
        raise self.retry(exc=exc, countdown=30)
```

```python
# after
@soniq.job(name="emails.send_email")
async def send_email(to: str, subject: str):
    ...

@soniq.job(name="payments.process_payment", retries=5, queue="urgent", retry_delay=30)
async def process_payment(order_id: int):
    # Just raise. Soniq retries by default.
    ...
```

Notes:

- **`async def` is preferred.** Soniq workers run on `asyncio`. Sync handlers also work, but they run on a bounded thread pool (8 threads per Soniq instance by default), so each sync job uses a thread slot.
- **No `bind=True`.** In Celery, `bind=True` gives you `self` so you can call `self.retry(...)` or read `self.request.id`. In Soniq, a raised exception triggers the retry. To read the job id or attempt number, declare a `ctx: JobContext` parameter or register hooks. See "Hooks and JobContext instead of `bind=True`".
- **Enqueue validates arguments.** Soniq checks the keyword arguments against the handler signature at enqueue time. Pass `validate=MyArgsModel` (a Pydantic model) to check them against a model instead.

### Retries

Celery offers two retry styles: explicit `self.retry(...)` and `autoretry_for=(...)`. Soniq has one: an uncaught exception triggers a retry, governed by the decorator's `retries`, `retry_delay`, `retry_backoff`, and `retry_max_delay`.

```python
# Celery: explicit
@app.task(bind=True, max_retries=5)
def fetch(self, url):
    try:
        return requests.get(url).json()
    except RequestException as exc:
        raise self.retry(exc=exc, countdown=2 ** self.request.retries)

# Celery: autoretry
@app.task(autoretry_for=(RequestException,), retry_backoff=True, max_retries=5)
def fetch(url):
    return requests.get(url).json()

# Soniq
@soniq.job(name="jobs.fetch", retries=5, retry_backoff=True, retry_delay=1, retry_max_delay=60)
async def fetch(url: str):
    async with httpx.AsyncClient() as client:
        return (await client.get(url)).json()
```

To skip the retry for a specific exception class, catch it in the handler and return normally. Soniq treats "completed without raising" as success.

## Enqueueing: `delay` / `apply_async` -> `enqueue`

Celery has two entry points: `task.delay(*args, **kwargs)` (no options) and `task.apply_async(args=[...], kwargs={...}, **options)`. Soniq has one: `await soniq.enqueue(task, **kwargs)`. A job object also has `await task.enqueue(**kwargs)` and `await task.options(queue="urgent").enqueue(**kwargs)`.

```python
# before
send_email.delay("dev@example.com", subject="Hi")
send_email.apply_async(
    args=["dev@example.com"],
    kwargs={"subject": "Hi"},
    countdown=30,
    queue="urgent",
    priority=5,
)

# after
await soniq.enqueue(send_email, to="dev@example.com", subject="Hi")
await soniq.enqueue(
    send_email,
    to="dev@example.com",
    subject="Hi",
    scheduled_at=30,  # seconds from now
    queue="urgent",
    priority=5,
)
```

Three differences:

- **`enqueue` is async.** Every call needs `await`. If your producer code is sync (a Django view, a Click command), wrap the call in `asyncio.run(soniq.enqueue(...))`. This suits one-shot scripts. Soniq has no sync enqueue helper, so a long-lived sync framework needs an async producer.
- **Job arguments are keyword-only.** `await soniq.enqueue(send_email, "dev@example.com")` is not valid. Pass `to="dev@example.com"`. The keyword-only style separates Soniq options (`queue`, `priority`, `scheduled_at`) from your job arguments.
- **`countdown` is `scheduled_at` with a number or a `timedelta`.** `eta` is `scheduled_at` with a timezone-aware `datetime`. Soniq rejects naive datetimes. There is no `delay=` option.

### Cross-service enqueue

If your producer lives in a different service from your worker (so it can't import the task function), Soniq can enqueue by string job name. Put the job arguments in `args=`:

```python
await soniq.enqueue("billing.send_invoice", args={"order_id": 123})
```

Celery does the same with `app.send_task("billing.tasks.send_invoice", kwargs={"order_id": 123})`. By default Soniq raises `SONIQ_UNKNOWN_TASK_NAME` if the producer has not registered the name. See [Cross-service jobs](../guides/cross-service-jobs.md) for the producer settings.

## Job lifecycle and statuses

Celery has many states (`PENDING`, `STARTED`, `SUCCESS`, `FAILURE`, `RETRY`, `REVOKED`). `PENDING` also covers task ids that Celery does not know. Soniq has four stored states:

| Soniq status | Meaning |
|---|---|
| `queued` | Row exists, waiting for a worker. Includes "scheduled for later" and "sleeping between retries". |
| `processing` | A worker is currently running this job. |
| `done` | Handler returned successfully. |
| `cancelled` | Cancelled before it ran via `soniq.cancel_job(...)`. |

There is no `failed` row state. A failure either re-queues the job (back to `queued`) or, after exhausting retries, moves it into a separate `soniq_dead_letter_jobs` table. The dead-letter table is durable, queryable, and replayable via `soniq.dead_letter.replay(dlq_id)`. `soniq.get_job(job_id)` still finds a dead-lettered job and reports its `status` as `"dead_letter"`.

Unlike Celery, an unknown id is not reported as pending. `soniq.get_job(job_id)` returns `None` for an id that does not exist. Store the `job_id` that `enqueue` returns.

## Results and the result backend

Celery's result backend is a separate store (Redis, a database and others) that `task.delay(...).get()` reads. Soniq stores the return value in the same row as the job, in `soniq_jobs.result` (`JSONB`). To read it:

```python
job_id = await soniq.enqueue(compute_total, order_id=123)
# ... later ...
result = await soniq.get_result(job_id)         # the value, or None if the job is not done
job = await soniq.get_job(job_id)               # returns the full row dict
```

Differences from Celery:

- **Return values must be JSON-serialisable.** Celery 4 and later default to JSON, and Celery can also use pickle. Soniq is JSON-only. If a return value is not JSON, the job still completes and the stored result is `None`. Return dicts, lists, strings, numbers and booleans, or store large outputs in S3 or Postgres and return a reference.
- **There is no blocking `.get(timeout=...)`.** Poll with `soniq.get_result` inside `asyncio.wait_for`. Soniq does not ship a blocking helper, because waiting for a job turns a queue into an RPC.
- **Results expire.** Soniq deletes a completed job after `result_ttl` seconds. The default is 300 (5 minutes), and `SONIQ_RESULT_TTL` sets it. Read the result within that time, or store it yourself.

## Periodic tasks: Beat -> the scheduler

Celery Beat is a separate process, and running two Beats at once duplicates tasks. Soniq replaces it with the `soniq scheduler` service. The schedulers elect a leader with a Postgres advisory lock. You can run several replicas for failover, and only the leader fires jobs.

```python
# before
app.conf.beat_schedule = {
    "rollup-every-hour": {
        "task": "reports.rollup",
        "schedule": crontab(minute=0),
    },
}
```

```python
# after
@soniq.periodic(cron="0 * * * *", name="reports.hourly_rollup")
async def rollup():
    ...

# also valid:
@soniq.periodic(every=timedelta(minutes=15), name="cache.warm_caches")
async def warm_caches():
    ...
```

Pass exactly one of `cron=` (a 5-field cron expression) or `every=`. The scheduler does nothing until you run `soniq scheduler`.

## Hooks and JobContext instead of `bind=True`

If you used `self.request.id` or `self.request.retries` in Celery, use a `ctx: JobContext` parameter. It has `job_id`, `job_name`, `attempt`, `max_attempts`, `queue`, `worker_id`, `scheduled_at` and `created_at`. Do not pass it when you enqueue.

```python
from soniq import JobContext

@soniq.job(name="payments.process_payment")
async def process_payment(order_id: int, ctx: JobContext):
    logger.info("processing", job_id=ctx.job_id, attempt=ctx.attempt)
```

Hooks run around every job. Soniq calls them with positional arguments, in this order:

```python
@soniq.before_job
async def log_start(job_name, job_id, attempt):
    logger.info("starting", job_id=job_id, attempt=attempt)

@soniq.after_job
async def log_done(job_name, job_id, duration_ms):
    logger.info("done", job_id=job_id, duration_ms=duration_ms)

@soniq.on_error
async def report(job_name, job_id, error, attempt):
    sentry_sdk.capture_message(error, extras={"job_id": job_id})
```

A failing hook is logged and ignored. It never blocks the job.

## Workers: `celery worker` -> `soniq worker`

The CLI shapes line up cleanly:

| Celery | Soniq |
|---|---|
| `celery -A myapp worker --loglevel=info` | `soniq worker myapp.jobs:soniq` |
| `celery -A myapp worker -Q urgent,default` | `soniq worker --queues urgent,default` |
| `celery -A myapp worker -c 8` | `soniq worker --concurrency 8` |
| `celery -A myapp worker --max-tasks-per-child=100` | not needed - Soniq workers don't fork |
| `celery -A myapp inspect active` | `soniq status --jobs` (lists jobs; `soniq inspect` lists workers) |
| `celery -A myapp inspect stats` | `soniq inspect` or `soniq status` |
| `celery -A myapp control shutdown` | Send `SIGTERM` to the worker process |

Model differences:

- **No prefork pool.** Soniq concurrency comes from `asyncio` tasks, not child processes. For CPU-bound work, run one worker process per core, each at low concurrency.
- **Sync handlers are bounded.** They run on a thread pool of `sync_handler_pool_size` threads (default 8, env `SONIQ_SYNC_HANDLER_POOL_SIZE`). There is no `--max-tasks-per-child`.
- **The app path replaces `-A myapp`.** `soniq worker myapp.jobs:soniq` imports the module so the `@soniq.job()` decorators register. Without an app path, set `SONIQ_JOBS_MODULES` (comma-separated module paths) or pass `--jobs-modules`.
- **Delivery is at-least-once.** If a worker crashes mid-job, another worker runs the job again. Write idempotent handlers, as you would with Celery `acks_late`.

## Things Celery has that Soniq does not

Check this list before you start. If your code depends heavily on these features, the migration needs more work, or Celery is the better fit:

- **Chains, chords, groups.** Soniq has no `a | b | c` and no "wait for N jobs, then run a callback". For a chain, the last step of job A enqueues job B. For a chord, each job increments a counter row, and the job that reaches N enqueues the finaliser. This is more code than `chain(...)`.
- **Multiple result backends.** Soniq stores results in `soniq_jobs.result`. There is no backend choice.
- **Pickle serialisation.** Soniq is JSON-only. Serialise complex objects yourself, or store them outside the queue.
- **`task.signature(...)` and `.s(...)` partials.** Build the kwargs dict yourself and pass it to `enqueue`.
- **Worker remote control (`broadcast`, `cancel_consumer`).** Send `SIGTERM` instead.
- **Config-driven `task_routes`.** Soniq takes the queue from the decorator (`queue=`) or the `enqueue` call. A consumer-side `route_map` setting maps a name prefix to a queue.
- **Exactly-once delivery.** Neither Celery nor Soniq offers it. Postgres alone cannot guarantee it.
- **Other brokers and ecosystem.** Soniq works only with PostgreSQL (plus SQLite and in-memory backends for tests). Celery has a larger community and more integrations.

## Migration sequence

Celery and Soniq share no state, so you can move one module at a time.

1. Install Soniq alongside Celery.
2. Run `soniq setup myapp.jobs:soniq` against your Postgres database. The command creates the Soniq tables and is idempotent.
3. Pick a low-risk job module first. Choose idempotent jobs with low traffic.
4. Convert its tasks: `@app.task` becomes `@soniq.job(name=...)`, sync functions become `async def`, and `task.delay(...)` becomes `await soniq.enqueue(...)`.
5. Run a Soniq worker for that module next to your Celery workers.
6. Watch the dashboard, your error tracker and latency for about a week.
7. Migrate the next module.
8. Convert Beat schedules to `@soniq.periodic(...)`. Start `soniq scheduler`. You can run Beat and the Soniq scheduler together while you move schedules one by one.
9. Stop enqueuing into Celery. Wait for the Celery queues to empty. Stop the Celery workers and Beat.
10. Remove `celery`, `kombu` and `flower`. Remove Redis if nothing else uses it.

## See also

- [Quickstart](../quickstart.md) - five minutes from `pip install` to first job
- [Migrate from RQ](from-rq.md) - the same guide for RQ users
- [Transactional enqueue](../guides/transactional-enqueue.md) - enqueue a job in the same database transaction as your data, which a Redis or RabbitMQ broker cannot do
- [Tutorial: defining jobs](../tutorial/01-defining-jobs.md) - the decorator API in depth
- [Going to production](../production/going-to-production.md)
