---
description: Move from Celery or RQ to Soniq, a Python background job library on PostgreSQL. Compare trade-offs, then pick the Celery or RQ migration guide.
---

# Migrating to Soniq

Soniq is a Python background job library that uses PostgreSQL as its only broker. Most teams that consider it already run Celery or RQ with Redis. Teams usually migrate for one of these reasons:

- **Operating two services is one too many.** PostgreSQL is already in your stack, so Soniq removes the need for Redis.
- **Transactional enqueue.** Soniq can insert the job inside the same transaction as your business write. No "row exists but the job never fired" bugs. Celery and RQ cannot do this - the broker is a separate service.
- **Async-native workers.** Soniq workers run on `asyncio` from the ground up. If your app is FastAPI / Starlette / async SQLAlchemy, this matches the rest of your stack.
- **Built-in dashboard, scheduler, and dead-letter queue.** No Flower, no rq-scheduler, no RQ Dashboard to deploy and authenticate separately.

Soniq also has costs:

- **Throughput ceiling.** Postgres row locking has limits. If you need sustained 10k+ jobs/sec, Redis-backed queues remain a better fit.
- **Python workers only.** Workers must be Python. Other languages can enqueue with the `soniq_enqueue` SQL function, but a Go, Node or Rust worker cannot consume jobs. Use a broker such as RabbitMQ or Kafka for those workers.
- **No DAG orchestration.** Soniq runs individual jobs. If you need dependency graphs and complex workflows, Prefect or Airflow handle that better.

If those tradeoffs are acceptable, pick the guide that matches your current stack:

- [Migrate from Celery](from-celery.md) - the most common path. Concept-by-concept mapping (`@soniq.task` -> `@soniq.job()`, Beat -> the scheduler, result backend -> `soniq_jobs.result`), what Celery has that Soniq does not (chains/chords, pickle, `bind=True`), and a module-at-a-time migration sequence.
- [Migrate from RQ](from-rq.md) - shorter. RQ's surface is small, so the mapping is mostly mechanical: registries collapse into a four-state `status` column, `Retry(...)` becomes `@soniq.job(retries=...)`, and the workers/scheduler/dashboard are all built in instead of separate packages.

## What's the same on both paths

- **Both migrations are rewrites at the call sites.** Soniq's API surface is intentionally smaller than Celery's; the mapping below is the supported path. Update each `delay(...)` / `q.enqueue(...)` call to `await soniq.enqueue(...)` rather than aliasing the old API.
- **`soniq setup` is idempotent.** Run it once per deploy. Re-running it is safe.
- **Soniq and your old queue can coexist.** They share no state. Run them side by side, migrate one job module at a time, and only stop the old workers once their queue is drained.
- **Sync handlers still work.** Both Celery and RQ are sync-first. Soniq prefers `async def` but runs sync handlers on a bounded thread pool. You can move to async step by step.
- **Periodic jobs need a separate `soniq scheduler` deployment.** Replaces Celery Beat or rq-scheduler. Replicas elect one leader through a PostgreSQL advisory lock. The other replicas wait, so failover is automatic.

## See also

- [Quickstart](../quickstart.md) - five minutes from `pip install` to first job
- [Why Soniq?](../why-soniq.md) - the longer case for switching
- [Transactional enqueue](../guides/transactional-enqueue.md) - the killer feature neither Celery nor RQ can offer
