<p align="left">
  <img src="docs/assets/logo.svg" width="72" alt="Soniq logo" />
</p>

# Soniq

Background jobs for Python. Powered by the Postgres you already have. Nothing else to maintain.

[![PyPI version](https://img.shields.io/pypi/v/soniq)](https://pypi.org/project/soniq/)
[![Python versions](https://img.shields.io/pypi/pyversions/soniq)](https://pypi.org/project/soniq/)
[![License](https://img.shields.io/github/license/abhinavs/soniq)](https://github.com/abhinavs/soniq/blob/main/LICENSE)
[![Tests](https://img.shields.io/github/actions/workflow/status/abhinavs/soniq/test.yml?label=tests)](https://github.com/abhinavs/soniq/actions)

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
soniq setup                                       # one-time: create tables
SONIQ_JOBS_MODULES=jobs soniq worker --concurrency 4   # run a worker
python jobs.py                                    # enqueue
```

Four steps. Define a job, set up the database, run a worker, enqueue. `SONIQ_JOBS_MODULES` tells the worker which modules to import so it can find your `@app.job` definitions.

## Transactional enqueue

The reason most teams choose a Postgres-backed queue. Enqueue a job inside the same transaction as your business writes - if the transaction rolls back, the job never existed:

```python
# Borrow a connection from Soniq's asyncpg pool. Any active asyncpg
# connection works here; it does not have to be Soniq's pool. If your
# app already has its own pool (or a SQLAlchemy session), pass that
# connection instead - see docs/guides/transactional-enqueue.md.
async with app.backend.acquire() as conn:
    async with conn.transaction():
        # Your business write. The order row only becomes visible once
        # this transaction commits.
        await conn.execute(
            "INSERT INTO orders (id, total) VALUES ($1, $2)",
            order_id, total,
        )

        # Same connection -> same transaction. The job row goes into
        # soniq_jobs as part of *this* COMMIT, not a separate one.
        # connection=conn is the only thing that differs from a normal
        # enqueue() call.
        await app.enqueue(
            send_invoice,
            connection=conn,
            order_id=order_id,
        )

        # If anything inside this `with` block raises, both writes
        # roll back together. The order is never created without the
        # follow-up job, and the job is never created for an order
        # that does not exist.
```

No Redis-backed queue can do this - their writes happen on a different system, so you need an outbox table and a drain process to keep them in sync. Soniq's job table lives in your Postgres, so a single transaction covers both.

Soniq is at-least-once, not exactly-once: a worker can crash after running your handler but before marking the row done, and the heartbeat sweep will requeue it. Handlers should be idempotent. See [docs/guides/cross-service-jobs.md](docs/guides/cross-service-jobs.md) for the full delivery-semantics details.

## Why Soniq

Most Python job queues force you to run Redis or RabbitMQ alongside your database. That is another service to deploy, monitor, back up, and debug when things go wrong at 3am.

Soniq uses your existing PostgreSQL. One dependency. One place your data lives. One thing to back up.

| Feature                      | Soniq | Celery      | RQ     |
| ---------------------------- | ----- | ----------- | ------ |
| No Redis / broker dependency | Yes   | No          | No     |
| Async native                 | Yes   | Partial     | No     |
| **Transactional enqueue**    | Yes   | No          | No     |
| Setup complexity             | Low   | High        | Medium |
| Built-in dashboard           | Yes   | No (Flower) | No     |
| Dead-letter queue            | Yes   | No          | No     |

## When NOT to use Soniq

- **You need 10k+ jobs/sec sustained throughput.** PostgreSQL row locking has limits. Redis-backed queues like Celery or Arq are built for this.
- **You need cross-language workers.** Soniq is Python-only. If your workers are in Go or Node, use RabbitMQ or similar.
- **You are not using PostgreSQL.** The production backend requires PostgreSQL.
- **You need DAG-based workflow orchestration.** Soniq runs individual jobs, not pipelines. Look at Prefect or Airflow.

## Features

- **Retries with backoff** - configurable delays, exponential backoff, per-attempt delay lists
- **Dead-letter queue** - failed jobs preserved for inspection and manual replay
- **Job priorities** - lower number = higher priority, processed first
- **Scheduled jobs** - run at a specific time or after a delay
- **Recurring jobs** - cron-based recurring schedules with `@app.periodic(cron="0 * * * *")`
- **Transactional enqueue** - atomic with your database writes
- **Multiple queues** - route jobs by type, run dedicated workers per queue
- **Middleware hooks** - `before_job`, `after_job`, `on_error` for logging, metrics, tracing
- **Worker heartbeat** - auto-detect crashed workers, requeue their jobs
- **Deduplication** - prevent duplicate jobs with `dedup_key` or `unique=True`
- **CLI + dashboard** - `setup`, `worker`, `scheduler`, `status`, `inspect`, dead-letter management; web UI

## Dashboard

A built-in web dashboard for inspecting jobs, queues, and recent failures. Read-only by default; opt in to retry/cancel/delete actions with `SONIQ_DASHBOARD_WRITE_ENABLED=true` (which also requires `SONIQ_DASHBOARD_API_KEY` as a safety interlock).

<p align="left">
  <img src="docs/assets/soniq_dashboard.png" width="800" alt="Soniq dashboard showing recent jobs, queue stats, and 24h performance metrics" />
</p>

```bash
pip install "soniq[dashboard]"
soniq dashboard                       # binds 127.0.0.1:6161
```

## Install extras

```bash
pip install soniq              # core + scheduler + Prometheus sink (Postgres backend)
pip install soniq[full]        # everything below
pip install soniq[dashboard]   # web dashboard (FastAPI + uvicorn)
pip install soniq[webhooks]    # webhook delivery + signing
pip install soniq[logging]     # structlog integration
```

The default install is batteries-included: `croniter` (so `@periodic` and the recurring scheduler work out of the box) and `prometheus_client` (so `PrometheusMetricsSink` is importable) ship with core. They stay dormant unless wired - the scheduler only runs if you start it, and the default `MetricsSink` is `NoopMetricsSink`.

## Documentation

- [Quickstart](docs/quickstart.md)
- [Tutorial: defining jobs](docs/tutorial/01-defining-jobs.md)
- [FastAPI integration](docs/guides/fastapi.md)
- [Going to production](docs/production/going-to-production.md)
- [Deployment](docs/production/deployment.md)
- [CLI reference](docs/cli/commands.md)
- [API reference](docs/api/soniq.md)

### For AI coding agents

- [`AGENTS.md`](AGENTS.md) - canonical patterns, anti-patterns, and the four mistakes agents most often make.
- [`docs/llms.txt`](docs/llms.txt) - curated index of the canonical pages, following the [llms.txt convention](https://llmstxt.org).
- [`docs/llms-full.txt`](docs/llms-full.txt) - the six canonical pages concatenated for one-shot context loading.

## License

MIT
