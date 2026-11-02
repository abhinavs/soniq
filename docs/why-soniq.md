---
description: Why use Soniq for Python background jobs - PostgreSQL instead of Redis, transactional enqueue, a built-in dashboard, and the limits to know before you pick it.
---

# Why use Soniq for Python background jobs

Soniq is a Python background job library that stores jobs in PostgreSQL instead of Redis. Use it if you run FastAPI or another asyncio app on Postgres and want jobs to commit with your data. This page is for two readers:

- A developer who has never used a job queue and wants to know if they need one.
- A developer who uses Celery or RQ and wants to know whether to switch.

The [home page](index.md) has the short version.

## What is a background job?

Consider an API endpoint that creates a user and sends a welcome email. Sending the email can take a second or two, because the SMTP server is slow or the email service limits your rate. You do not want the user staring at a spinner during a signup.

A background job lets you say "do this later, in another process" and return immediately. The user gets their response in 50ms. The email goes out a moment later, on a separate worker. If the email fails, the worker retries it. If your API restarts, the email is not lost - it is sitting in a queue waiting to be picked up.

Background jobs are how every non-trivial web app handles slow side effects: emails, webhooks, file processing, report generation, syncing data to third-party systems, anything that should not block a request.

## Why not FastAPI's BackgroundTasks?

If you use FastAPI, you already have `BackgroundTasks`, and for a lot of apps it is where background work starts. You add `background_tasks.add_task(send_welcome, user.id)` to a route, and the function runs right after the response goes out. No extra setup.

The catch is that the task lives in the memory of the web process. It is not written anywhere. So:

- **A deploy or a crash drops it.** If Uvicorn restarts while ten welcome emails wait to run, those ten emails never go out, and nothing records that they were missed.
- **A failure is final.** If the email provider times out, the task raises, the error goes to your logs, and that is the end of it. No retry.
- **You can't see it.** There is no list of pending tasks and no history of failed ones. "Did the invoice for order 4711 go out?" has no answer short of searching the logs.
- **It runs on your web process.** An async task shares the event loop that serves your requests, and a sync one takes a thread from the same pool your sync routes use. You can't move the work to another machine.

`BackgroundTasks` is fine for work you don't mind losing, like a cache warm-up or an analytics ping. For anything a user would notice if it went missing, you want the task written down before the request returns.

That is the change Soniq makes. `await soniq.enqueue(send_welcome, user_id=user.id)` writes a row to Postgres. The job survives a restart, retries on failure, shows up in the dashboard and in the dead-letter queue if it keeps failing, and it can run in the same process (with the FastAPI lifespan helper) or in a separate worker. The route code barely changes. The [FastAPI guide](guides/fastapi.md#coming-from-backgroundtasks) has the before-and-after diff.

## Why Postgres instead of Redis?

Most Python job queues - Celery, RQ, Arq, Dramatiq - default to Redis as the broker. Redis is fast and mature, but you must run it as a separate service.

**Operational cost.** Another service to provision, monitor, back up, scale, and patch. Another connection string to thread through your code. If you already run Postgres for your application data, running Redis just to track jobs is a real overhead, especially for small and medium teams.

**Data lives in two places.** Your application data is in Postgres. Your job state is in Redis. They have different backup stories, different disaster-recovery stories, and different consistency models. Restoring from a backup means restoring two systems and hoping they are in sync.

**No transactions across the boundary.** This is the largest cost. If you want to "create an order and enqueue an invoice job atomically", you cannot - the order goes to Postgres, the job goes to Redis, and there is no transaction that spans both. Teams typically work around this with the [transactional outbox pattern](https://microservices.io/patterns/data/transactional-outbox.html): write to a Postgres outbox table, then have a separate process drain it into Redis. It works, but it adds a table and a process.

Soniq avoids this by storing jobs as rows in the Postgres database your app already uses. You back up, monitor and commit the jobs and the data together.

```python
async with soniq.backend.acquire() as conn:
    async with conn.transaction():
        await conn.execute("INSERT INTO orders (id, total) VALUES ($1, $2)", order_id, total)
        await soniq.enqueue(send_invoice, connection=conn, order_id=order_id)
        # Both writes commit together, or neither does.
```

This pattern is called transactional enqueue.

## What you get out of the box

The table compares what each tool includes by default. The [comparison page](compare.md) has dated sources for other tools. Check each tool's own documentation before you decide.

| Feature | Soniq | Celery + Redis | RQ |
| --- | --- | --- | --- |
| Dashboard / web UI | Built-in | Third-party (Flower) | Third-party (RQ Dashboard) |
| Prometheus metrics | Built-in | Plugin | Manual |
| Deduplication | Built-in (`unique=True`) | Plugin | Manual |
| Transactional enqueue | Native | Outbox pattern | Outbox pattern |
| Webhook delivery primitives | Built-in | Custom code | Custom code |
| Dead-letter queue | Built-in | Plugin / manual | Manual |
| Scheduled / recurring jobs | Built-in | Celery Beat | RQ Scheduler |
| Extra broker required | None | Redis | Redis |
| Result storage | Native (Postgres) | Configurable backend | Configurable backend |

Celery and RQ are mature tools. For a Python web app on Postgres that needs background work, Soniq has fewer parts to run.

## Async-native workers

If your app is FastAPI, Starlette, async SQLAlchemy, or similar, Soniq fits the rest of your stack. Workers run on `asyncio`. Handlers are `async def`. The `enqueue()` call is a coroutine.

Celery and RQ run sync handlers. If the rest of your codebase uses `await`, async workers need no sync-to-async bridge.

## Limits of Soniq

Know these limits before you choose Soniq.

- **Sustained 10k+ jobs/sec.** Postgres row-level locking under contention has limits. If you need that throughput, Redis-backed queues remain a better fit.
- **Cross-language workers.** Soniq is Python-only. If your workers are in Go, Node, or Rust, you need a real broker (RabbitMQ, Kafka, NATS).
- **Non-Postgres databases.** SQLite and an in-memory backend exist for dev and tests, but the production backend is PostgreSQL only. There is no MySQL backend, no SQL Server backend, and there will not be one.
- **DAG-based workflow orchestration.** Soniq runs individual jobs, not pipelines with dependency graphs. For that, look at Prefect, Airflow, or Temporal.

## Who should use Soniq

Soniq fits if:

- You are building a FastAPI app or another asyncio Python app.
- You are using PostgreSQL.
- You want background jobs but you do not want another service to run.
- Your throughput is "thousands of jobs per minute" or less, not "tens of thousands per second".
- You want transactional enqueue, or you have had bugs where a database row exists but its job never ran.

If that is you, the next step is the [quickstart](quickstart.md).

If you come from another job queue, read:

- [Migrating from Celery](migration/from-celery.md)
- [Migrating from RQ](migration/from-rq.md)
