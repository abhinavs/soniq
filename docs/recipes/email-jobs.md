# Recipe: Email Jobs

A production pattern for sending transactional emails with retries, idempotency, and a dedicated queue.

## The job

```python
from soniq import Soniq
from soniq.job import JobContext

eq = Soniq(database_url="postgresql://localhost/myapp")


@eq.job(queue="emails", max_retries=3, retry_delay=[5, 30, 300])
async def send_welcome_email(user_id: int, ctx: JobContext):
    user = await get_user(user_id)

    # Idempotent: skip if already sent
    if user.welcome_email_sent:
        return

    await send_email(to=user.email, template="welcome")
    await mark_welcome_sent(user_id)
```

## Enqueuing

```python
@app.post("/users")
async def create_user(name: str, email: str):
    user = await save_user(name=name, email=email)
    await eq.enqueue(send_welcome_email, user_id=user.id)
    return {"id": user.id}
```

## Why this works

**Idempotent check.** The first thing the job does is check whether the email was already sent. If a worker crashes after `send_email()` but before marking the job done, the job will run again. The `welcome_email_sent` flag prevents sending duplicates.

**Escalating retry delays.** `retry_delay=[5, 30, 300]` means the first retry waits 5 seconds, the second waits 30, and the third waits 5 minutes. This gives transient failures (DNS hiccups, rate limits) time to resolve without hammering the mail provider.

**Dedicated queue.** Putting emails on their own `"emails"` queue lets you run a separate worker with its own concurrency. Email sends are I/O-bound and shouldn't compete with CPU-heavy jobs for worker slots.

**JobContext.** The `ctx` parameter is injected automatically. Use `ctx.attempt` if you want to log which retry you're on, or `ctx.job_id` for correlation.

## Running the worker

```bash
soniq worker --queues emails --concurrency 2
```

Keep email worker concurrency low to respect rate limits. If your provider allows 10 requests/second, two concurrent workers with a small batch size is plenty.

## Variations

**Escalating with backoff.** For longer retry windows, use `retry_backoff=True` with a base delay:

```python
@eq.job(queue="emails", max_retries=5, retry_delay=10, retry_backoff=True, retry_max_delay=3600)
async def send_email_with_backoff(user_id: int):
    ...
```

**Transactional enqueue.** If you're creating the user row and enqueuing the email in the same request, wrap them in a transaction so neither happens without the other:

```python
await eq.ensure_initialized()
async with eq.backend.acquire() as conn:
    async with conn.transaction():
        user_id = await conn.fetchval("INSERT INTO users (...) VALUES (...) RETURNING id", ...)
        await eq.enqueue(send_welcome_email, connection=conn, user_id=user_id)
```
