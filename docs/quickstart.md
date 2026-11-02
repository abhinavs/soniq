---
description: Run your first Python background job with Soniq and PostgreSQL in five minutes. Install, define a job, set up the database, start a worker, enqueue.
---

# Quickstart: run a Python background job with PostgreSQL

Soniq is a Python background job library that uses PostgreSQL as its queue. This page takes you from `pip install` to a running worker in about 5 minutes.

## 1. Install

```bash
pip install soniq
```

You need a running PostgreSQL. If you do not have one, start one with Docker:

```bash
docker run -p 5432:5432 -e POSTGRES_PASSWORD=postgres postgres:18
```

> To try Soniq without PostgreSQL, run `pip install "soniq[sqlite]"`. Then pass a file path instead of a URL: `Soniq(database_url="dev.db")`. SQLite and the in-memory backend (`Soniq(backend="memory")`) are for local development and tests only. See the [testing guide](guides/testing.md#sqlite-for-integration-tests) and the [Backend](reference/glossary.md#backend) glossary entry.

## 2. Set the environment

The `soniq` command and your Python code both read the database URL from the environment. Set both variables in your shell:

```bash
export SONIQ_DATABASE_URL="postgresql://localhost/myapp"
export SONIQ_JOBS_MODULES=jobs
```

`SONIQ_JOBS_MODULES` lists the Python modules that the worker imports on startup. The import runs the `@soniq.job()` decorators, which register your jobs. Without it, the worker does not know which jobs exist.

## 3. Define a job

Create a file named `jobs.py`:

```python
# jobs.py
import asyncio
from soniq import Soniq

soniq = Soniq()

@soniq.job(name="emails.send_welcome")
async def send_welcome(to: str):
    print(f"Sending welcome email to {to}")

if __name__ == "__main__":
    async def main():
        await soniq.enqueue(send_welcome, to="dev@example.com")
        await soniq.close()

    asyncio.run(main())
```

The job has an explicit `name`. When you run `python jobs.py`, Python calls the file `__main__`. Without a fixed name, Soniq stores the job as `__main__.send_welcome`. The worker imports the file as `jobs`, so it cannot find that name.

## 4. Set up the database

```bash
soniq setup
```

This command creates the tables that Soniq needs. It is idempotent: you can run it more than once and it changes nothing the second time.

## 5. Start a worker

```bash
soniq worker --concurrency 4
```

The worker process must be able to import your job code. For cross-service setups and per-worker overrides, see [Job module discovery](getting-started/installation.md#job-module-discovery).

## 6. Enqueue a job

Open another terminal and run:

```bash
python jobs.py
```

The worker prints "Sending welcome email to dev@example.com". You now have a working background queue.

## What changes in production

Make these changes before you deploy.

- **Read settings from the environment.** Do not hardcode `database_url`. Soniq reads `SONIQ_DATABASE_URL` automatically.
- **Set `SONIQ_JOBS_MODULES`** so workers import your job functions on startup.
- **Run `soniq setup` once per deploy.** Do not run it from every replica. See [going to production](production/going-to-production.md).
- **Make handlers idempotent.** An idempotent handler gives the same end result when it runs more than once. Soniq guarantees *at-least-once* delivery. If a worker crashes after it runs your function but before it marks the job done, another worker runs the job again. To make a rerun harmless, use `INSERT ... ON CONFLICT DO UPDATE` instead of a plain insert. You can also check whether you already sent the email, or store an idempotency token with the side effect.
- **Tune timeouts.** Every job has a default timeout of 300 seconds. Override it per job: `@soniq.job(name="reports.build", timeout=600)`.

> **New to job queues?** The [tutorial](tutorial/01-defining-jobs.md) explains each term in depth. You do not need to learn all of this now.

## Next steps

To learn each concept, work through the [tutorial](tutorial/01-defining-jobs.md). It has six chapters and takes about 30 minutes.

If you already know job queues, go to one of these pages:

- [FastAPI guide](guides/fastapi.md) - the most common producer setup
- [Going to production](production/going-to-production.md) - the checklist for a healthy deploy
- [Migrating from Celery](migration/from-celery.md) or [from RQ](migration/from-rq.md)

---

*Working with an AI coding agent? Point it at [`AGENTS.md`](https://github.com/abhinavs/soniq/blob/main/AGENTS.md) and [`llms.txt`](llms.txt). They cover the canonical patterns and the mistakes agents make most often.*
