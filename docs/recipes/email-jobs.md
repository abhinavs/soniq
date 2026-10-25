---
description: Send transactional email as Python background jobs with Soniq and PostgreSQL: idempotent handlers, escalating retries, a dedicated queue, transactional enqueue.
---

# Recipe: Email Jobs

This recipe sends transactional email from Soniq background jobs on PostgreSQL. It uses retries, an idempotency check, and a dedicated queue.

## The job

```python
from soniq import JobContext, Soniq

soniq = Soniq(database_url="postgresql://localhost/myapp")


@soniq.job(name="emails.send_welcome_email", queue="emails", retries=3, retry_delay=[5, 30, 300])
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
    await soniq.enqueue(send_welcome_email, user_id=user.id)
    return {"id": user.id}
```

## Why this works

**Idempotent check.** The first thing the job does is check whether the email was already sent. If a worker crashes after `send_email()` but before marking the job done, Soniq runs the job again. The `welcome_email_sent` flag prevents sending duplicates.

**Escalating retry delays.** `retry_delay=[5, 30, 300]` means the first retry waits 5 seconds, the second waits 30, and the third waits 5 minutes. This gives transient failures (DNS hiccups, rate limits) time to resolve without hammering the mail provider.

**Dedicated queue.** Putting emails on their own `"emails"` queue lets you run a separate worker with its own concurrency. Email sends are I/O-bound. They must not compete with CPU-heavy jobs for worker slots.

**JobContext.** The worker injects the `ctx` parameter. Do not pass it to `enqueue`. Use `ctx.attempt` to log the attempt number, or `ctx.job_id` for correlation.

## Running the worker

```bash
soniq worker --queues emails --concurrency 2
```

Keep email worker concurrency low to respect provider rate limits. Two concurrent jobs are enough for a provider that allows 10 requests per second.

## Variations

**Escalating with backoff.** For longer retry windows, use `retry_backoff=True` with a base delay:

```python
@soniq.job(name="emails.send_email_with_backoff", queue="emails", retries=5, retry_delay=10, retry_backoff=True, retry_max_delay=3600)
async def send_email_with_backoff(user_id: int):
    ...
```

**Transactional enqueue.** To create the user row and enqueue the email together, use one transaction. Both writes then commit or roll back as a unit:

```python
await soniq.ensure_initialized()
async with soniq.backend.acquire() as conn:
    async with conn.transaction():
        user_id = await conn.fetchval("INSERT INTO users (...) VALUES (...) RETURNING id", ...)
        await soniq.enqueue(send_welcome_email, connection=conn, user_id=user_id)
```
