# Migrating to Soniq

Most teams considering Soniq are already running Celery + Redis or RQ + Redis. Both are solid tools. The reason to migrate is usually one of these:

- **Operating two services is one too many.** Postgres is already in your stack. Why run Redis just to track jobs?
- **Transactional enqueue.** Soniq can insert the job inside the same transaction as your business write. No "row exists but the job never fired" bugs. Celery and RQ cannot do this - the broker is a separate service.
- **Async-native workers.** Soniq workers run on `asyncio` from the ground up. If your app is FastAPI / Starlette / async SQLAlchemy, this matches the rest of your stack.
- **Built-in dashboard, scheduler, and dead-letter queue.** No Flower, no rq-scheduler, no RQ Dashboard to deploy and authenticate separately.

The cost is real, though, and we want to be honest about it:

- **Throughput ceiling.** Postgres row locking has limits. If you need sustained 10k+ jobs/sec, Redis-backed queues remain a better fit.
- **Python-only.** Soniq has no story for cross-language consumers. Use a broker (RabbitMQ, Kafka) if your workers are in Go, Node, or Rust.
- **No DAG orchestration.** Soniq runs individual jobs. If you need dependency graphs and complex workflows, Prefect or Airflow handle that better.

If those tradeoffs are acceptable, pick the guide that matches your current stack:

- [Migrate from Celery](from-celery.md) - the most common path. Concept-by-concept mapping (`@app.task` -> `@app.job()`, Beat -> the scheduler, result backend -> `soniq_jobs.result`), what Celery has that Soniq does not (chains/chords, pickle, `bind=True`), and a module-at-a-time migration sequence.
- [Migrate from RQ](from-rq.md) - shorter. RQ's surface is small, so the mapping is mostly mechanical: registries collapse into a four-state `status` column, `Retry(...)` becomes `@app.job(retries=...)`, and the workers/scheduler/dashboard are all built in instead of separate packages.

## What's the same on both paths

A few things are worth saying once rather than twice:

- **Both migrations are rewrites at the call sites.** Soniq's API surface is intentionally smaller than Celery's; the mapping below is the supported path. Plan to update each `delay(...)` / `q.enqueue(...)` call to `await app.enqueue(...)` rather than aliasing the old surface.
- **`soniq setup` is idempotent.** Run it once per database; safe to re-run on every deploy.
- **Soniq and your old queue can coexist.** They share no state. Run them side by side, migrate one job module at a time, and only stop the old workers once their queue is drained.
- **Sync handlers still work.** Both Celery and RQ are sync-first. Soniq prefers `async def` but runs sync handlers on a bounded thread pool. You can port to async incrementally.
- **Periodic jobs need a separate `soniq scheduler` deployment.** Replaces Celery Beat or rq-scheduler. Multiple replicas coordinate via a Postgres advisory lock; one leader, the rest idle, so failover is automatic.

## See also

- [Quickstart](../quickstart.md) - five minutes from `pip install` to first job
- [Why Soniq?](../why-soniq.md) - the longer case for switching
- [Transactional enqueue](../guides/transactional-enqueue.md) - the killer feature neither Celery nor RQ can offer
