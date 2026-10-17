# AGENTS.md

If you are an AI coding agent (Claude Code, Cursor, aider, ...) writing or editing code that uses Soniq, **read this file first**. It is the canonical, prescriptive brief - shorter than the docs, but covers the four things agents most often get wrong.

For the curated index of all docs, see `docs/llms.txt`. For one-shot context loading, see `docs/llms-full.txt` (the six canonical pages concatenated into one file).

## What Soniq is

Soniq is a Python background-job library backed by PostgreSQL. No Redis, no separate broker. Workers run on `asyncio`. Jobs are rows in `soniq_jobs`; the worker process claims them with `SELECT ... FOR UPDATE SKIP LOCKED`.

If you are coming from Celery, the closest analogy is "Celery, but with Postgres as both broker and result backend, async-native, and with `chains/chords` cut from scope". From RQ, "RQ, but Postgres-backed, decorator-required, and with the scheduler/dashboard built in".

## The canonical shape

Every Soniq codebase has the same four moving parts. Use these literal shapes unless the user has explicitly said otherwise.

### 1. Define a job

```python
# myapp/jobs.py
from soniq import Soniq

soniq = Soniq(database_url="postgresql://localhost/myapp")

@soniq.job()
async def send_email(to: str, subject: str, body: str):
    ...
```

Always use parens: `@soniq.job()`, never `@soniq.job`. The bare form is not supported.

Decorator kwargs (all optional): `name=`, `retries=`, `priority=`, `queue=`, `unique=`, `retry_delay=`, `retry_backoff=`, `retry_max_delay=`, `timeout=`, `validate=`. See `docs/api/jobs.md`.

### 2. Enqueue a job

```python
# Inside async code:
job_id = await soniq.enqueue(send_email, to="dev@example.com", subject="Hi", body="Hello")

# Same thing, from the job itself. A type checker checks the arguments.
job_id = await send_email.enqueue(to="dev@example.com", subject="Hi", body="Hello")

# With enqueue options:
job_id = await send_email.options(queue="urgent", priority=1).enqueue(to="dev@example.com", subject="Hi", body="Hello")

# Inside sync code (rare; usually a script or a sync framework callback):
import asyncio
asyncio.run(soniq.enqueue(send_email, to="dev@example.com", subject="Hi", body="Hello"))
```

Both forms are supported. `soniq.enqueue(fn, ...)` is not deprecated.

Job arguments are **keyword-only**. `await soniq.enqueue(send_email, "dev@example.com")` is invalid - pass `to="dev@example.com"`.

### 3. Set up the database

```bash
soniq setup myapp.jobs:soniq
```

Idempotent. Run it once per deploy (not from every replica). Creates all tables Soniq needs.

### 4. Run a worker

```bash
soniq worker myapp.jobs:soniq --concurrency 4
```

`myapp.jobs:soniq` is `module:attribute` for your `Soniq` instance. The worker imports the module, so the `@soniq.job()` decorators register the functions. It uses the database URL of the instance. `--database-url` or `SONIQ_DATABASE_URL` override that URL.

The environment-variable form also works:

```bash
SONIQ_DATABASE_URL="postgresql://localhost/myapp" \
SONIQ_JOBS_MODULES="myapp.jobs" \
soniq worker --concurrency 4
```

`SONIQ_JOBS_MODULES` adds more job modules to the app module. Without an app path, it is required: it tells the worker which modules to import.

Optional: `--queues default,urgent` to scope a worker to specific queues. Default is "all queues".

### Single-process FastAPI

```python
from fastapi import FastAPI
from soniq.integrations.fastapi import lifespan

app = FastAPI(lifespan=lifespan(soniq, run_worker=True, concurrency=2))
```

Use `run_worker=False` (the default) when the worker runs as `soniq worker`. Each Uvicorn process starts its own worker.

## Top four mistakes agents make

These are the patterns Soniq doesn't accept. If you are about to write one of these, stop and use the alternative.

### 1. Bare `@soniq.job` (no parens)

```python
# WRONG
@soniq.job
async def my_job(): ...

# RIGHT
@soniq.job()
async def my_job(): ...
```

The bare form was deliberately removed before the 1.0 release. There is no shim.

### 2. Positional args to `enqueue`

```python
# WRONG
await soniq.enqueue(send_email, "dev@example.com", "Hi")

# RIGHT
await soniq.enqueue(send_email, to="dev@example.com", subject="Hi")
```

The keyword-only style avoids ambiguity between Soniq's framework options (`queue`, `delay`, `priority`, `scheduled_at`) and your job's own arguments.

`send_email.enqueue("dev@example.com")` raises `TypeError` too. A type checker does not catch it, because `ParamSpec` cannot express keyword-only arguments.

If your producer is talking to a job in another service (no import access), use the string-name form:

```python
await soniq.enqueue("billing.send_invoice", args={"order_id": 123})
```

### 3. Calling `enqueue` from sync code without `asyncio.run`

```python
# WRONG (in a sync function)
def create_order(...):
    soniq.enqueue(send_email, to="...")   # returns a coroutine, never runs

# RIGHT (one-shot script)
def main():
    asyncio.run(soniq.enqueue(send_email, to="..."))

# RIGHT (long-lived sync framework like Django views)
# ... use a sync helper or run an async producer; see docs/guides/fastapi.md for the FastAPI shape.
```

`enqueue` is `async def`. Forgetting to await it is the single most common silent-failure bug.

### 4. Returning non-JSON values from a handler

Soniq stores results in `soniq_jobs.result` (`JSONB`). Return values must be JSON-serialisable.

```python
# WRONG
@soniq.job()
async def fetch_user(user_id: int) -> User:
    return await User.get(user_id)        # Pydantic model, datetimes, UUIDs - won't serialise as-is

# RIGHT
@soniq.job()
async def fetch_user(user_id: int) -> dict:
    user = await User.get(user_id)
    return user.model_dump(mode="json")    # or persist to DB and return {"user_id": user.id}
```

Dicts, lists, strings, numbers, booleans, `None`. If you need to return something larger or more structured, persist it out-of-band (S3, a row in your own table) and return a reference.

## Retries and idempotency

Soniq's delivery is **at-least-once**. A worker that crashes mid-handler causes the job to run again on another worker. Handlers must be idempotent - rerunning them must produce the same end state.

Common idempotency patterns:

- Database upserts: `INSERT ... ON CONFLICT DO UPDATE` instead of plain `INSERT`.
- Idempotency tokens: store the token alongside the side effect, check before sending.
- Check-then-act: `if already_sent: return` at the top of the handler.

Retries fire automatically on uncaught exceptions, governed by `@soniq.job(retries=N, retry_delay=..., retry_backoff=...)`. To suppress retries for a specific exception, catch it in the handler and return normally - "completed without raising" is success.

## Transactional enqueue (the killer feature)

If you are inserting business data and want a follow-up job to fire if and only if the insert commits, pass a `connection` to `enqueue`. With SQLAlchemy, pass the session:

```python
async with db.begin():
    db.add(order)
    await db.flush()
    await soniq.enqueue(send_invoice, connection=db, order_id=order.id)
```

`connection=` accepts an SQLAlchemy `AsyncSession` or `AsyncConnection` (asyncpg or psycopg driver, active transaction), a raw `asyncpg` connection from any pool, or a `psycopg.AsyncConnection`. With a raw asyncpg connection:

```python
async with soniq.backend.acquire() as conn:
    async with conn.transaction():
        await conn.execute("INSERT INTO orders (id, total) VALUES ($1, $2)", order_id, total)
        await soniq.enqueue(send_invoice, connection=conn, order_id=order_id)
```

If the transaction rolls back, the job is also rolled back. PostgreSQL only - SQLite/in-memory backends raise `ValueError`.

For Tortoise and your-own-pool patterns, see `docs/guides/transactional-enqueue.md`.

## Testing

```python
import pytest
from soniq.testing import drain
from myapp.jobs import send_email

@pytest.mark.soniq(app="myapp.jobs:soniq")   # load the jobs registered on your instance
async def test_signup(soniq_memory):
    await soniq_memory.enqueue(send_email, to="dev@example.com", subject="Hi", body="Hello")
    assert await drain(soniq_memory) == [None]
```

The `soniq_memory` and `soniq_pg` fixtures come from an opt-in plugin: add `pytest_plugins = ["soniq.testing.pytest_plugin"]` to `conftest.py`. Without the `soniq` marker, the fixture only knows jobs you define on it inside the test. See `docs/guides/testing.md`.

## Periodic jobs

Use `@soniq.periodic(...)`, not Celery Beat or rq-scheduler:

```python
from datetime import timedelta
from soniq import daily, every

@soniq.periodic(cron=daily().at("09:00"))
async def daily_report(): ...

@soniq.periodic(every=timedelta(minutes=15))
async def warm_caches(): ...
```

Pick exactly one of `cron=` or `every=`. Then run `soniq scheduler` as a separate process - it's the leader-elected service that fires due jobs. Without it, `@soniq.periodic` registrations are inert.

## Hooks (the replacement for Celery's `bind=True`)

If you need the current job id, attempt number, or wall-clock duration, use hooks instead of stuffing logic into the handler:

```python
@soniq.before_job
async def log_start(job_name, job_id, attempt): ...

@soniq.after_job
async def log_done(job_name, job_id, duration_ms): ...

@soniq.on_error
async def report(job_name, job_id, error, attempt): ...
```

Hooks are sync-or-async. A failing hook is logged and ignored - it never blocks job execution.

## CLI surface (for orchestration)

| Command | Purpose |
|---|---|
| `soniq setup [APP]` | Create/update schema. Idempotent. Run once per deploy. |
| `soniq worker [APP] [--concurrency N] [--queues a,b]` | Long-running worker process. |
| `soniq scheduler [APP]` | Long-running periodic-job dispatcher. Run one (more if you want failover - they leader-elect). |
| `soniq dashboard [--host 0.0.0.0] [--port 6161]` | Web UI for inspecting jobs and queues. Read-only by default. |
| `soniq inspect` | List active workers and their status. `--cleanup` removes stale records. |
| `soniq dead-letter list \| replay \| delete \| cleanup \| export` | DLQ management. |
| `soniq status [--verbose] [--jobs]` | Health summary, queue stats, recent jobs. |
| `soniq migrate-status` | Show applied vs pending migrations. |
| `soniq migrations export --alembic\|--sql` | Print core migrations as an Alembic revision or SQL. |

Full reference: `docs/cli/commands.md`.

## What Soniq does NOT have

If a user's request mentions one of these, push back rather than fabricating an API:

- **Chains, chords, groups** (Celery's `chain(a.s(), b.s())`). Workaround: enqueue the next job from the previous handler's body.
- **Pickle serialisation.** JSON only.
- **Multiple result backends.** Results go in `soniq_jobs.result`. There is no Redis/Memcached/database-backend choice.
- **`bind=True` / per-task `self`.** Use hooks or `JobContext` instead.
- **`task.signature(...)` / `.s(...)` partials.** Build the kwargs dict yourself.
- **Worker remote control / broadcast.** Send `SIGTERM`.
- **Config-driven `task_routes`.** Queue is on the decorator (`@soniq.job(queue=...)`) or the enqueue call.
- **Burst-mode workers** (RQ's `--burst`). Workers are long-lived. For one-shot batch processing, write a script that calls `await soniq.enqueue(...)` and let normal workers process.
- **Exactly-once delivery.** Postgres alone cannot guarantee it. Make handlers idempotent.

## When you don't know

If a user asks for something not covered here, look it up in this order:

1. `docs/api/<area>.md` - the authoritative reference.
2. `docs/guides/<topic>.md` - task-shaped, with end-to-end examples.
3. `docs/reference/glossary.md` - one-paragraph definitions for ambiguous terms.
4. `docs/llms.txt` - the curated full doc index.

Do not invent APIs. If the docs don't describe a feature, it doesn't exist - propose the closest thing that does, or tell the user it isn't supported.
