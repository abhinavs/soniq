# Quickstart

Get a job running in under 5 minutes.

## 1. Install

```bash
pip install soniq
```

You will need a running PostgreSQL. If you do not have one handy, `docker run -p 5432:5432 -e POSTGRES_PASSWORD=postgres postgres:16` will do.

## 2. Define a job

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

## 3. Set up the database

```bash
soniq setup
```

Creates the tables Soniq needs. The command is idempotent (safe to run more than once - re-running it does not break anything).

## 4. Start a worker

```bash
SONIQ_DATABASE_URL="postgresql://localhost/myapp" \
SONIQ_JOBS_MODULES="jobs" \
soniq worker --concurrency 4
```

`SONIQ_JOBS_MODULES` tells the worker which Python modules to import on startup, so the `@app.job` decorators have a chance to run and register the functions. Without it, the worker has no idea which jobs exist. The worker process also needs to be able to actually import your job code - see [Job module discovery](getting-started/installation.md#job-module-discovery) for cross-service setups and per-worker overrides.

## 5. Enqueue a job

In another terminal:

```bash
python jobs.py
```

The worker prints "Sending welcome email to dev@example.com". You have a working background queue.

## What changes in production

The code above works, but you will want to tighten a few things before deploying for real.

> **New to job queues?** The terms below (idempotent, at-least-once) are explained inline as they appear, and the [tutorial](tutorial/01-defining-jobs.md) covers each in depth. The quickstart is just here to show the mechanics - do not feel like you have to internalize all of this in one sitting.

- **Use environment variables** instead of hardcoding `database_url`. Soniq reads `SONIQ_DATABASE_URL` automatically.
- **Set `SONIQ_JOBS_MODULES`** so workers can import your job functions on startup.
- **Run `soniq setup` only once per deploy**, not from every replica's startup. See [going to production](production/going-to-production.md).
- **Make handlers idempotent** - meaning safe to run more than once with the same end result. Soniq guarantees *at-least-once* delivery: if a worker crashes after running your function but before marking the job done, the job will run again on another worker. This is normal for every job queue. The fix is to design your handler so a re-run is harmless: use database upserts (`INSERT ... ON CONFLICT DO UPDATE`) instead of plain inserts, check whether you already sent the email before sending it again, or store an idempotency token alongside the side effect.
- **Tune timeouts.** Every job has a default 300-second timeout. Override per-job with `@app.job(timeout=600)`.

## Where to go next

**Just starting out?** Work through the [tutorial](tutorial/01-defining-jobs.md) in order. It is six chapters, takes about 30 minutes, and walks through every concept you will need.

**Already comfortable with job queues?** Skip ahead based on what you are doing:

- [FastAPI guide](guides/fastapi.md) - the most common producer shape
- [Going to production](production/going-to-production.md) - the eight things that matter for a healthy deploy
- [Migrating from Celery](migration/from-celery.md) or [from RQ](migration/from-rq.md)

---

*Working with an AI coding agent? Point it at [`AGENTS.md`](https://github.com/abhinavs/soniq/blob/main/AGENTS.md) and [`llms.txt`](llms.txt) - they cover the canonical patterns and the mistakes agents most often make.*
