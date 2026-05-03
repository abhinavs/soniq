# Soniq

Background jobs for Python. Powered by the Postgres you already have. Nothing else to maintain.

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

app = Soniq(database_url="postgresql://localhost/myapp")

@app.job()
async def send_welcome(to: str):
    print(f"Sending welcome email to {to}")

if __name__ == "__main__":
    asyncio.run(app.enqueue(send_welcome, to="dev@example.com"))
```

```bash
soniq setup                          # one-time: create tables
SONIQ_JOBS_MODULES=jobs soniq worker  # run a worker
python jobs.py                       # enqueue
```

Four steps. Define a job, set up the database, run a worker, enqueue.

[Full quickstart guide](quickstart.md){ .md-button }

## Transactional enqueue, in one paragraph

The reason most teams pick a Postgres-backed queue. You can enqueue a job inside the same database transaction as your business write. If the order insert rolls back, the "send invoice" job never existed. No outbox table, no two-phase commit, no "row exists but the job never fired" bugs. No Redis-backed queue can do this - the broker is a separate service.

[See the transactional enqueue guide](guides/transactional-enqueue.md){ .md-button }

## Who is this for?

Soniq is a good fit if you are building a FastAPI, Django, or Flask app on PostgreSQL and need background jobs without adding infrastructure.

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
