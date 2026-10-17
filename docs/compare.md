# Soniq compared with other tools

This page compares Soniq with other Python tools for background work. It tells you when to pick each one, including when to pick something other than Soniq.

Last checked: 2026-10-16. Each claim about another tool comes from that tool's own docs or repository, linked in the table. Projects change. Check the source before you decide.

## Summary table

| Tool | Storage | Source |
| --- | --- | --- |
| Soniq | PostgreSQL (SQLite and in-memory for dev and tests) | [README](https://github.com/abhinavs/soniq#readme) |
| Procrastinate | PostgreSQL 13+, sync and async, Django integration | [GitHub](https://github.com/procrastinate-org/procrastinate), [docs](https://procrastinate.readthedocs.io/en/stable/) |
| PgQueuer | PostgreSQL 14+, LISTEN/NOTIFY, transactional enqueue | [GitHub](https://github.com/janbjorge/PgQueuer) |
| Oban for Python | PostgreSQL 14+, asyncio, paid Pro tier | [GitHub](https://github.com/oban-bg/oban-py) |
| Chancy | PostgreSQL, plugins for workflows and dashboard | [GitHub](https://github.com/tktech/chancy) |
| DBOS Transact | PostgreSQL, durable workflows | [GitHub](https://github.com/dbos-inc/dbos-transact-py) |
| Hatchet | PostgreSQL, workflow engine, Python/TypeScript/Go/Ruby SDKs | [GitHub](https://github.com/hatchet-dev/hatchet), [docs](https://docs.hatchet.run/home) |
| `django.tasks` | Task API only, no production worker included | [Django docs](https://docs.djangoproject.com/en/6.0/topics/tasks/) |
| Celery | RabbitMQ, Redis, SQS and others | [Celery docs](https://docs.celeryq.dev/en/stable/getting-started/introduction.html) |
| RQ | Redis or Valkey | [RQ docs](https://python-rq.org/) |
| arq | Redis, asyncio | [arq docs](https://arq-docs.helpmanual.io/) |
| Taskiq | Pluggable brokers, asyncio | [Taskiq docs](https://taskiq-python.github.io/), [brokers](https://taskiq-python.github.io/available-components/brokers.html) |

## What Soniq offers

These points are true of Soniq in this repository:

- Jobs are rows in PostgreSQL. You can enqueue a job in the same transaction as your own data with `connection=`.
- Workers use `asyncio`. Handlers are `async def`.
- A web dashboard, a dead-letter queue, deduplication, and recurring jobs ship with the package. See the [README](https://github.com/abhinavs/soniq#readme).
- Soniq runs single jobs. It has no workflow graphs. See [Why Soniq?](why-soniq.md).
- Soniq is Python only. Its status is Beta (see `pyproject.toml`).

## PostgreSQL-backed Python tools

### Procrastinate

Procrastinate stores tasks in PostgreSQL 13+. It supports sync and async code, periodic tasks, retries, and task locks. It has a Django integration.

Pick Procrastinate when:

- Your project uses Django and you want the integration its docs describe.
- You have sync code and want sync tasks.

Pick Soniq when:

- You want a web dashboard and a dead-letter queue in the package.
- Your stack is async-first.

### PgQueuer

PgQueuer uses PostgreSQL 14+ with LISTEN/NOTIFY, `FOR UPDATE SKIP LOCKED`, and transactional enqueue. Its README lists a terminal dashboard (`pgq dashboard`), Prometheus metrics, and drivers for asyncpg and psycopg.

Pick PgQueuer when:

- You use psycopg and do not want asyncpg.
- You want a small tool with a terminal dashboard.

Pick Soniq when:

- You want a web dashboard and a dead-letter queue.
- You already use asyncpg.

### Oban for Python

Oban for Python runs on PostgreSQL 14+ and Python 3.12+. It shares a schema with Oban for Elixir. Its README lists workflows, rate-limited concurrency, and unique jobs under the paid Oban Pro tier.

Pick Oban when:

- You also run Oban in Elixir and want both to share one schema.
- You want to pay for the Pro features, such as workflows and rate limits.

Pick Soniq when:

- You need Python 3.10 or 3.11.
- You want deduplication and the dashboard without a paid tier.

### Chancy

Chancy is async-first and needs only PostgreSQL. Its README lists priorities, retries, timeouts, global rate limits, global uniqueness, and cancellation. Workflows, cron, and the dashboard are plugins. A worker can run threads, processes, sub-interpreters, and asyncio jobs.

Pick Chancy when:

- You need global rate limits or memory limits.
- You want workers that run CPU-heavy jobs in processes or sub-interpreters.
- You want a plugin model for workflows.

Pick Soniq when:

- You want the dashboard and the dead-letter queue in the main package.
- You want the transactional enqueue guides and migration guides from Celery and RQ.

### DBOS Transact

DBOS checkpoints each workflow step in PostgreSQL, so a workflow resumes after a crash. It also has durable queues, cron scheduling, and durable sleep.

Pick DBOS when:

- Your work has many steps and a finished step must not run again after a crash.
- You need workflows that wait for events.

Pick Soniq when:

- Your work is single, independent jobs.
- You want a plain job queue and you make handlers idempotent yourself.

### Hatchet

Hatchet is an orchestration engine with PostgreSQL as the durability layer. It offers DAGs, durable tasks, rate limits, a web UI, and SDKs for Python, TypeScript, Go, and Ruby. You can use Hatchet Cloud or self-host it.

Pick Hatchet when:

- You need DAG workflows, fair scheduling, or workers in other languages.
- You want a managed service option.

Pick Soniq when:

- You want a library that runs in your own processes, with PostgreSQL as the only other component.
- You do not need workflow graphs.

## Framework and Redis-based tools

### `django.tasks`

Django 6.0 has a tasks API (`@task`, `.enqueue()`). Its docs say Django does not provide a worker. Production use needs a third-party backend.

Pick `django.tasks` when:

- You use Django and want the standard API with a backend that you choose.

Pick Soniq when:

- You want a worker, retries, and a dashboard now. Soniq does not ship a `django.tasks` backend. You call the Soniq API directly.

### Celery

Celery supports RabbitMQ, Redis, and SQS. It has Canvas workflows (chains, groups), periodic tasks, many result backends, and prefork, eventlet, gevent, and thread pools. Clients exist for other languages.

Pick Celery when:

- You need chains and groups, or a broker shared with other languages.
- You want a long-established ecosystem.

Pick Soniq when:

- You want PostgreSQL as the only store and transactional enqueue.
- You want async handlers. See [Migrating from Celery](migration/from-celery.md).

### RQ

RQ uses Redis or Valkey. It supports scheduling (`enqueue_at`, `enqueue_in`), repeating jobs, retries, and stored results.

Pick RQ when:

- You already run Redis and want a small library with sync code.

Pick Soniq when:

- You want to remove Redis and enqueue inside a database transaction. See [Migrating from RQ](migration/from-rq.md).

### arq

arq is an asyncio job queue on Redis. It has deferred jobs, retries, cron jobs, job IDs for uniqueness, and job abort. Its docs say a job can run more than once.

Pick arq when:

- You use Redis and want a small asyncio library.

Pick Soniq when:

- You want PostgreSQL storage, a dashboard, and a dead-letter queue.

### Taskiq

Taskiq is an asyncio task manager with replaceable brokers. Its broker list names RabbitMQ, Redis, NATS, SQS, and Kafka as official. The only PostgreSQL broker on that list is a third-party package.

Pick Taskiq when:

- You want to choose the broker, such as NATS or Kafka, and keep an async API.

Pick Soniq when:

- You want PostgreSQL support in the main package.

## Where Soniq is the wrong choice

- You need DAG workflows: use Hatchet, DBOS, or Celery Canvas.
- You need workers in other languages: use Hatchet or Celery.
- You need more throughput than PostgreSQL row locking gives: use a Redis or RabbitMQ tool. See the [honest limits](why-soniq.md#honest-limits).
- You need sync handlers: use Procrastinate, RQ, or Celery.
