# Migrate from Celery

This guide is for teams running Celery (with Redis or RabbitMQ as the broker) who want to move to Soniq. Celery's API surface is large and idiomatic, so this migration takes more thought than the RQ one. The good news is that nearly every Celery concept has a direct equivalent in Soniq - the differences are mostly that Soniq is async-first and that some Celery features (chains, chords, multiple result backends) are deliberately not in scope.

Treat this as a rewrite at the call sites: `task.delay(arg)` becomes `await app.enqueue(task, arg=arg)`, `@app.task` becomes `@app.job(...)`. The migration sequence below assumes a clean cutover.

## Concept map

The fast version. Every row is expanded in the sections that follow.

| Celery | Soniq | Notes |
|---|---|---|
| `Celery("app", broker="redis://...")` | `Soniq(database_url="postgresql://...")` | No broker. Jobs live in your Postgres. |
| `@app.task` | `@app.job()` | Always called with parentheses. |
| `@app.task(bind=True, max_retries=5)` | `@app.job(retries=5)` | No `bind=True` - hooks replace `self`. See "Hooks vs `bind=True`". |
| `task.delay(arg)` | `await app.enqueue(task, arg=arg)` | Always `await`ed. Keyword-only args. |
| `task.apply_async(args=[...], kwargs={...}, countdown=30)` | `await app.enqueue(task, ..., delay=30)` | Soniq's `enqueue` is the one entry point. |
| `task.apply_async(eta=datetime)` | `await app.enqueue(task, scheduled_at=datetime, ...)` | |
| `task.apply_async(queue="urgent")` | `await app.enqueue(task, queue="urgent", ...)` | Or set `queue=` on `@app.job(...)` for a default. |
| `result = task.delay(...); result.get()` | `result = await app.get_result(job_id)` | See "Results and the result backend". |
| `result.status` | `(await app.get_job(job_id))["status"]` | Status values differ - see "Job lifecycle". |
| `app.control.revoke(task_id)` | `await app.cancel_job(job_id)` | |
| `celery -A app worker -Q urgent,default` | `soniq worker --queues urgent,default` | |
| `celery -A app beat` | `soniq scheduler` + `@app.periodic(...)` | Beat replaced by a leader-elected scheduler service. |
| `celery -A app inspect active` | `soniq inspect` | |
| Flower | `soniq dashboard` | Built in, runs on port 8000 by default. |
| `chain(a.s(), b.s(), c.s())` | Not supported. Enqueue the next job from the previous one's body. | |
| `chord(group, callback)` | Not supported. Use a counter row + a finaliser job. | |
| `@app.task(autoretry_for=(IOError,), retry_backoff=True)` | `@app.job(retries=N, retry_backoff=True, retry_delay=N)` | Soniq retries on every uncaught exception by default; narrow it inside the handler if you need to. |

## App and broker: there is no broker

Celery makes you pick a broker. Soniq doesn't - your Postgres is the broker.

```python
# before
from celery import Celery
app = Celery("myapp", broker="redis://localhost:6379/0", backend="redis://localhost:6379/1")

# after
from soniq import Soniq
app = Soniq(database_url=os.environ["DATABASE_URL"])
```

Two things to notice:

- **No separate result backend.** Soniq stores results in the same `soniq_jobs` row as the job itself (in a `result` JSONB column). If you previously relied on a separate Redis instance for `backend=`, that infrastructure goes away.
- **The `database_url` is almost certainly already in your `.env`** for your ORM. The migration usually deletes `BROKER_URL` and `RESULT_BACKEND` rather than adding anything.

## Defining tasks: `@app.task` -> `@app.job(...)`

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
@app.job()
async def send_email(to: str, subject: str):
    ...

@app.job(retries=5, queue="urgent", retry_delay=30)
async def process_payment(order_id: int):
    # Just raise. Soniq retries by default.
    ...
```

Notes:

- **`async def` is preferred.** Soniq workers run on `asyncio`; sync handlers still work but run on a bounded thread pool, so you pay a context-switch and a thread slot per job.
- **No `bind=True`.** In Celery, `bind=True` gives you `self` so you can call `self.retry(...)` or read `self.request.id`. In Soniq, retries happen automatically on raised exceptions, and contextual data (current job id, attempt number, registered metadata) is available through hooks rather than a per-task `self`. See "Hooks vs `bind=True`" below.
- **Type hints matter.** Pair `@app.job(validate=MyArgsModel)` with a Pydantic model to validate args at enqueue time - Celery has no equivalent and it catches a lot of "wrong shape" bugs in CI.

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
@app.job(retries=5, retry_backoff=True, retry_delay=1, retry_max_delay=60)
async def fetch(url: str):
    async with httpx.AsyncClient() as client:
        return (await client.get(url)).json()
```

If you want to suppress retries for a specific exception class, catch it in the handler and return normally - "completed without raising" is what Soniq treats as success.

## Enqueueing: `delay` / `apply_async` -> `enqueue`

Celery has two: `task.delay(*args, **kwargs)` (positional + kwargs, no options) and `task.apply_async(args=[...], kwargs={...}, **options)` (everything explicit). Soniq has one: `await app.enqueue(task, **kwargs)`.

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
await app.enqueue(send_email, to="dev@example.com", subject="Hi")
await app.enqueue(
    send_email,
    to="dev@example.com",
    subject="Hi",
    delay=30,
    queue="urgent",
    priority=5,
)
```

Three differences worth flagging:

- **`enqueue` is async.** Every call is `await`ed. If your producer code is sync (a Django view, a Click command), wrap it - `asyncio.run(app.enqueue(...))` for one-shot scripts, or use Soniq's sync helper if you have a long-lived sync producer.
- **Job arguments are keyword-only.** `await app.enqueue(send_email, "dev@example.com")` is not valid. Pass `to="dev@example.com"`. The keyword-only style means there's never ambiguity between Soniq's framework options (`queue`, `delay`, `priority`) and your job's arguments.
- **`countdown` is `delay`.** Same semantics, different name. `eta` is `scheduled_at` and accepts a `datetime`.

### Cross-service enqueue

If your producer lives in a different service from your worker (so it can't import the task function), Soniq lets you enqueue by string task name:

```python
await app.enqueue("billing.tasks.send_invoice", order_id=123)
```

Celery requires the same setup via `app.send_task("billing.tasks.send_invoice", kwargs={"order_id": 123})`. Same idea, friendlier surface.

## Job lifecycle and statuses

Celery exposes an open-ended set of states (`PENDING / STARTED / SUCCESS / FAILURE / RETRY / REVOKED / ...`) and `PENDING` famously means "we have no idea". Soniq's contract is tighter:

| Soniq status | Meaning |
|---|---|
| `queued` | Row exists, waiting for a worker. Includes "scheduled for later" and "sleeping between retries". |
| `processing` | A worker is currently running this job. |
| `done` | Handler returned successfully. |
| `cancelled` | Cancelled before it ran via `app.cancel_job(...)`. |

There is no `failed` row state. A failure either re-queues the job (back to `queued`) or, after exhausting retries, moves it into a separate `soniq_dead_letter_jobs` table. The dead-letter table is durable, queryable, and replayable via `app.dead_letter.replay(dlq_id)`.

This is closer to RQ's lifecycle than Celery's, and it has one important consequence: **`status="queued"` does not distinguish "not yet picked up" from "we have never heard of this job"**. If you want strict "did this job ever exist" semantics, store the `job_id` you got back from `enqueue` and use `app.get_job(job_id)` - that returns `None` for unknown ids.

## Results and the result backend

Celery's result backend is a separate Redis (or database) where `task.delay(...).get()` polls for outputs. Soniq stores the return value in the same row as the job, in `soniq_jobs.result` (`JSONB`). To read it:

```python
job_id = await app.enqueue(compute_total, order_id=123)
# ... later ...
result = await app.get_result(job_id)         # returns the value or None
job = await app.get_job(job_id)               # returns the full row dict
```

A few caveats vs. Celery:

- **Return values must be JSON-serialisable.** Celery's default serialisers (pickle, JSON) handle more shapes; Soniq is JSON-only on purpose. Return dicts, lists, strings, numbers, booleans - or persist large outputs to S3/Postgres yourself and return a reference.
- **There is no blocking `.get(timeout=...)`.** Async polling is your tool: `await asyncio.wait_for(_poll(job_id), timeout=30)`. We deliberately don't ship a blocking helper because most "I want to wait for this job" patterns are an anti-pattern in async code (you've turned a queue into an RPC).
- **Results live as long as the job row.** Set up a retention policy (`SONIQ_DONE_JOB_RETENTION_DAYS`) if you produce a lot of jobs.

## Periodic tasks: Beat -> the scheduler

Celery's Beat is a separate process that lives in its own deployment slot, often with its own headaches around clock drift and the "two beats running at once" failure mode. Soniq replaces it with a `soniq scheduler` service that uses a Postgres advisory lock for leader election - run two or three replicas if you want failover, only one will fire jobs at a time.

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
@app.periodic(cron="0 * * * *")
async def rollup():
    ...

# also valid:
@app.periodic(every=timedelta(minutes=15))
async def warm_caches():
    ...
```

Both `cron=` and `every=` are accepted; they're mutually exclusive on a single registration.

## Hooks vs `bind=True`

If you used `self.request.id`, `self.retries`, or other request-context fields in Celery, you'll want Soniq's hooks:

```python
@app.before_job
async def log_start(ctx):
    logger.info("starting", job_id=ctx.job_id, attempt=ctx.attempts)

@app.after_job
async def log_done(ctx):
    logger.info("done", job_id=ctx.job_id, duration_ms=ctx.duration_ms)

@app.on_error
async def report(ctx, exc):
    sentry_sdk.capture_exception(exc, extras={"job_id": ctx.job_id})
```

Hooks fire around every job, so you don't repeat `self.request.id` boilerplate inside each handler. The `ctx` object carries job id, task name, attempt number, queue, args, and timing.

## Workers: `celery worker` -> `soniq worker`

The CLI shapes line up cleanly:

| Celery | Soniq |
|---|---|
| `celery -A myapp worker --loglevel=info` | `SONIQ_JOBS_MODULES=myapp.jobs soniq worker` |
| `celery -A myapp worker -Q urgent,default` | `soniq worker --queues urgent,default` |
| `celery -A myapp worker -c 8` | `soniq worker --concurrency 8` |
| `celery -A myapp worker --max-tasks-per-child=100` | not needed - Soniq workers don't fork |
| `celery -A myapp inspect active` | `soniq inspect` |
| `celery -A myapp inspect stats` | `soniq inspect` |
| `celery -A myapp control shutdown` | `SIGTERM` (graceful), `SIGKILL` (immediate) |

A few model differences:

- **No prefork pool.** Celery's `--pool=prefork` exists because Python sync code blocks the event loop; Soniq is async-first, so concurrency comes from `asyncio` tasks rather than child processes. If you have CPU-bound work, run multiple worker processes (one per core), each at low concurrency.
- **Sync handlers are bounded.** They run on a thread pool whose size you set per-worker. There's no `--max-tasks-per-child`.
- **`SONIQ_JOBS_MODULES`** is the equivalent of `-A myapp` - a comma-separated list of module paths the worker imports so registrations execute. Either set the env var or pass `--jobs-modules`.

## Things Celery has that Soniq does not

Be honest about this before you start the migration. If your codebase leans heavily on these, the migration is more work, or it's the wrong call:

- **Chains, chords, groups.** Soniq has no built-in `a | b | c` or "wait for these N jobs, then run this finaliser" constructs. The straightforward replacement is "the last step of job A enqueues job B" for chains, and "increment a counter row, fire the finaliser when it hits N" for chords. This is fine for most pipelines and clarifies what's actually happening, but it's more code than `chain(...)`.
- **Multiple result backends.** Soniq stores results in `soniq_jobs.result`. There is no Redis/Memcached/database-backend choice.
- **Pickle serialisation.** Soniq is JSON-only. If you pickle complex objects, you'll need to serialise differently or persist them out-of-band.
- **`task.signature(...)` / `.s(...)` partials.** Build the kwargs dict yourself and pass it to `enqueue`.
- **Worker remote control (`broadcast`, `cancel_consumer`, etc.).** Out of scope. Send a `SIGTERM`.
- **`task_routes` config-driven routing.** Soniq picks the queue from the decorator (`@app.job(queue=...)`) or the `enqueue` call (`queue=...`). There is no separate routing config layer.

## Migration sequence

The path that's worked for teams:

1. **Install Soniq alongside Celery.** Both can coexist - they don't share state.
2. **Run `soniq setup`** against your Postgres database. This creates all soniq-owned tables in one shot.
3. **Pick a low-stakes job module first.** Something idempotent, with low traffic, and not on the critical path. Convert its tasks: `@app.task` -> `@app.job()`, sync -> `async def`, `task.delay(...)` -> `await app.enqueue(...)`.
4. **Run a Soniq worker for that queue** alongside your existing Celery workers. Keep enqueueing into both for one deploy if you want a safety net, but it's usually cleaner to flip the call sites and only enqueue into Soniq.
5. **Bake.** Watch the dashboard, watch your error tracker, watch latency. A week is a sensible soak.
6. **Migrate the next module.** Repeat. Each module is independent.
7. **Convert Beat schedules** to `@app.periodic(...)` and stand up a `soniq scheduler` deployment. You can run Beat and the Soniq scheduler simultaneously while you migrate periodic tasks one at a time.
8. **Drain Celery.** Stop enqueuing into Celery. Wait for the existing queue to empty. Stop Celery workers and Beat.
9. **Remove the dependencies.** Delete `celery`, `kombu`, `flower`, and (if nothing else uses it) Redis from your stack.

The migration is genuinely incremental: no flag day, no big-bang cutover. Each module flips when it's ready.

## See also

- [Quickstart](../quickstart.md) - five minutes from `pip install` to first job
- [Transactional enqueue](../guides/transactional-enqueue.md) - the killer feature Celery cannot offer
- [Tutorial: defining jobs](../tutorial/01-defining-jobs.md) - the decorator API in depth
- [Going to production](../production/going-to-production.md)
