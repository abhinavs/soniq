# Soniq

Background jobs for FastAPI and any asyncio app, stored in the Postgres you already have. No Redis, no broker, nothing else to run.

## Why Soniq?

**No Redis. No extra services.**
Soniq uses the PostgreSQL database you already have. There is no separate broker to provision, monitor, or pay for. One less thing to break at 2 AM.

**Your jobs and your data live in the same place.**
If your database is backed up, your job history is backed up. If your database is in a transaction, your job can be too. The order row and the "send invoice" job land in the same commit, or neither of them does.

**Batteries included.**
Retries, scheduling, deduplication, a built-in dashboard, Prometheus metrics, dead-letter queue, webhook delivery - all in the package. No plugins required.

**Simple to learn, simple to run.**
One `pip install`, one `soniq setup`, one `soniq worker`. That is the whole setup.

[Read the full case for Soniq](why-soniq.md){ .md-button } [Quickstart in 5 minutes](quickstart.md){ .md-button .md-button--primary }

## How it works

When you call `enqueue()`, Soniq saves the job as a row in your Postgres database. A worker process watches that database for new rows. When it finds one, it runs your function. No broker, no message bus - just Postgres doing what it does best.

Under the hood, Soniq uses Postgres's native `LISTEN/NOTIFY` for instant pickup (latency is typically under 10ms) and `SELECT ... FOR UPDATE SKIP LOCKED` so multiple workers can compete for jobs safely. You do not need to think about any of that to use it - but if you want the deeper picture, see [how it works under the hood](_internals/architecture.md).

Your job data lives in the same database as the rest of your application. Backed up together. Monitored together. Transacted together.

## Quickstart

```bash
pip install soniq
```

```python
# jobs.py
import asyncio
from soniq import Soniq

soniq = Soniq()

@soniq.job(name="send_welcome")
async def send_welcome(to: str):
    print(f"Sending welcome email to {to}")

if __name__ == "__main__":
    asyncio.run(soniq.enqueue(send_welcome, to="dev@example.com"))
```

```bash
export SONIQ_DATABASE_URL="postgresql://localhost/myapp"

soniq setup                              # one-time: create tables
soniq worker jobs:soniq --concurrency 4  # run a worker
python jobs.py                           # enqueue (in another terminal)
```

Four steps. Define a job, set up the database, run a worker, enqueue. `jobs:soniq` tells the worker where your Soniq instance is.

[Full quickstart guide](quickstart.md){ .md-button }

## Transactional enqueue, in one paragraph

The reason most teams pick a Postgres-backed queue. You can enqueue a job inside the same database transaction as your business write. If the order insert rolls back, the "send invoice" job never existed. No outbox table, no two-phase commit, no "row exists but the job never fired" bugs. No Redis-backed queue can do this - the broker is a separate service.

With SQLAlchemy async, pass your session:

```python
async with db.begin():
    db.add(order)
    await db.flush()
    await soniq.enqueue(send_invoice, connection=db, order_id=order.id)
```

[See the transactional enqueue guide](guides/transactional-enqueue.md){ .md-button }

## Coming from FastAPI BackgroundTasks

`BackgroundTasks` runs the task inside the web process after the response is sent. If that process stops before the task finishes, for example when a deploy stops it after a short grace period, the task is lost. With Soniq the task is a row in Postgres, and you can still run the worker in the same process:

```python
soniq = Soniq()
app = FastAPI(lifespan=lifespan(soniq, run_worker=True))

@app.post("/users")
async def create_user(name: str):
    user = await save_user(name=name)
    await soniq.enqueue(send_welcome, user_id=user.id)   # was: background_tasks.add_task(...)
    return {"id": user.id}
```

[See the FastAPI guide](guides/fastapi.md#coming-from-backgroundtasks){ .md-button }

## Who is this for?

Soniq is a good fit if you are building a FastAPI or other asyncio app on PostgreSQL and need background jobs without adding infrastructure.

If you are processing 10,000+ jobs per second, need cross-language workers, or need DAG-based workflow orchestration, a different tool is probably the right call - see [when NOT to use Soniq](#when-not-to-use-soniq) below.

## Coming from Celery or RQ?

If you already run Celery or RQ, you know what is painful: a separate broker to operate, the `.delay()` vs `.apply_async()` surface, configuring result backends, deploying Flower for a UI. Soniq has direct answers to each of those.

- [Migrating from Celery](migration/from-celery.md) - concept-by-concept mapping, what Celery has that Soniq does not, module-at-a-time sequence
- [Migrating from RQ](migration/from-rq.md) - shorter; RQ's surface is small, so the mapping is mechanical

## When NOT to use Soniq

- **You need 10k+ jobs/sec sustained throughput.** PostgreSQL row locking has limits. Redis-backed queues like Celery or Arq are built for this.
- **You need cross-language workers.** Soniq is Python-only. If your workers are in Go or Node, use RabbitMQ or similar.
- **You are not using PostgreSQL.** The production backend requires PostgreSQL.
- **You need DAG-based workflow orchestration.** Soniq runs individual jobs, not pipelines. Look at Prefect or Airflow.

## Where to next

- [Why Soniq?](why-soniq.md) - the longer case, with a comparison table
- [Compare tools](compare.md) - Soniq next to Celery, RQ, arq and the other Postgres-backed queues
- [Benchmarks](benchmarks.md) - throughput and latency numbers, with the method
- [Stability and versioning](stability.md) - what we keep stable between releases
- [Quickstart](quickstart.md) - five minutes from `pip install` to first job
- [Tutorial](tutorial/01-defining-jobs.md) - six chapters, ~30 minutes, covers every Soniq concept
- [Going to production](production/going-to-production.md) - the eight things that matter
- [Reference](reference/index.md) - Python API, CLI, configuration

## For AI coding agents

Pointing Cursor, Claude Code, aider, or another agent at this project? Three files are written for them:

- [`AGENTS.md`](https://github.com/abhinavs/soniq/blob/main/AGENTS.md) - the canonical agent brief: patterns, anti-patterns, and the four mistakes agents most often make.
- [`llms.txt`](llms.txt) - curated index of the canonical pages, following the [llms.txt convention](https://llmstxt.org).
- [`llms-full.txt`](llms-full.txt) - the canonical pages concatenated for one-shot context loading.

---

*Working with an AI agent? Point it at [`AGENTS.md`](https://github.com/abhinavs/soniq/blob/main/AGENTS.md) and [`llms.txt`](llms.txt).*
