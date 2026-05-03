# Why Soniq?

This page is for two readers:

- The developer who has never used a background job queue and is trying to figure out if they need one.
- The developer who has used Celery, RQ, or similar, and is trying to figure out whether to switch.

If you are in a hurry, the [home page](index.md) has the four-bullet version.

## What is a background job, anyway?

Imagine your API endpoint creates a user and sends a welcome email. Sending the email might take a second or two - the SMTP server is slow, or the third-party email service is rate-limiting you. You do not want the user staring at a spinner during a signup.

A background job lets you say "do this later, in another process" and return immediately. The user gets their response in 50ms. The email goes out a moment later, on a separate worker. If the email fails, the worker retries it. If your API restarts, the email is not lost - it is sitting in a queue waiting to be picked up.

Background jobs are how every non-trivial web app handles slow side effects: emails, webhooks, file processing, report generation, syncing data to third-party systems, anything that should not block a request.

## Why Postgres instead of Redis?

Most Python job queues - Celery, RQ, Arq, Dramatiq - default to Redis as the broker. Redis is fast and battle-tested, but it is also a separate service you have to run, and that has costs.

**Operational cost.** Another service to provision, monitor, back up, scale, and patch. Another connection string to thread through your code. Another thing to break at 2 AM. If you already run Postgres for your application data, running Redis just to track jobs is a real overhead, especially for small and medium teams.

**Data lives in two places.** Your application data is in Postgres. Your job state is in Redis. They have different backup stories, different disaster-recovery stories, and different consistency models. Restoring from a backup means restoring two systems and hoping they are in sync.

**No transactions across the boundary.** This is the big one. If you want to "create an order and enqueue an invoice job atomically", you cannot - the order goes to Postgres, the job goes to Redis, and there is no transaction that spans both. Teams typically work around this with the [transactional outbox pattern](https://microservices.io/patterns/data/transactional-outbox.html): write to a Postgres outbox table, then have a separate process drain it into Redis. It works but it is real complexity.

Soniq sidesteps all of this by storing jobs as rows in the same Postgres database your app already uses. The jobs and the data are backed up together, monitored together, and - the part that matters most - transacted together.

```python
async with app.backend.acquire() as conn:
    async with conn.transaction():
        await conn.execute("INSERT INTO orders (id, total) VALUES ($1, $2)", order_id, total)
        await app.enqueue(send_invoice, connection=conn, order_id=order_id)
        # Both writes commit together, or neither does.
```

That single pattern - transactional enqueue - is the reason most teams pick Soniq.

## What you get out of the box

Many alternatives require add-ons or custom code for what Soniq ships in the box.

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

The point is not that Celery and RQ are bad - they are excellent at what they do. It is that for the common case - a Python web app on Postgres that needs background work - Soniq removes a lot of moving parts.

## Async-native workers

If your app is FastAPI, Starlette, async SQLAlchemy, or similar, Soniq fits the rest of your stack. Workers run on `asyncio`. Handlers are `async def`. The `enqueue()` call is a coroutine.

Celery is sync at heart and grew an async story in 5.x; RQ is sync. Both can run async code, but they were not designed for it. If everything else in your codebase is `await`-ing, async-native workers are one less mental switch.

## Honest limits

The home page lists these too. They belong on this page as well, because the reader who sees what a tool is bad at takes seriously what it claims to be good at.

- **Sustained 10k+ jobs/sec.** Postgres row-level locking under contention has limits. If you need that throughput, Redis-backed queues remain a better fit.
- **Cross-language workers.** Soniq is Python-only. If your workers are in Go, Node, or Rust, you need a real broker (RabbitMQ, Kafka, NATS).
- **Non-Postgres databases.** SQLite and an in-memory backend exist for dev and tests, but the production backend is PostgreSQL only. There is no MySQL backend, no SQL Server backend, and there will not be one.
- **DAG-based workflow orchestration.** Soniq runs individual jobs, not pipelines with dependency graphs. For that, look at Prefect, Airflow, or Temporal.

## Who is this for?

You will likely have a good time with Soniq if:

- You are building a Python web app (FastAPI, Django, Flask, Litestar, anything else).
- You are using PostgreSQL.
- You want background jobs but you do not want another service to run.
- Your throughput is "thousands of jobs per minute" or less, not "tens of thousands per second".
- You are interested in the transactional-enqueue pattern, or you have hit "row exists but job never fired" bugs before.

If that is you, the next step is the [quickstart](quickstart.md). It takes five minutes.

If you are coming from another job queue, jump straight to:

- [Migrating from Celery](migration/from-celery.md)
- [Migrating from RQ](migration/from-rq.md)
