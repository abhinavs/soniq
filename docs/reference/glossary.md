# Glossary

One-paragraph definitions for the words Soniq's docs use.

## Job

A unit of work. Either the function you decorate with `@app.job` (the **handler**), or a specific row in `soniq_jobs` representing one scheduled invocation of that function (the **job instance**). Most of the docs say "job" for both and the meaning is clear from context. When ambiguity matters, "handler" and "job row" are the disambiguating terms.

## Handler

The Python function that runs when a job is processed. Registered via `@app.job`.

## Task name

The string identifier under which a handler is registered. By default Soniq derives it from `f"{module}.{qualname}"` (Celery-style). Override with `@app.job(name="...")`. The task name is the wire protocol for cross-service deployments - producers refer to handlers by name, not by import.

## TaskRef

A typed stub that lets a producer enqueue a job by name without importing the consumer's code. Created via `task_ref("billing.send_invoice", InvoiceArgs)`. Use it for cross-service producer/consumer setups where the producer should not own the handler implementation. See [cross-service jobs](../guides/cross-service-jobs.md).

## Worker

A long-running process that polls (or receives `NOTIFY` for) jobs, claims them via `SELECT ... FOR UPDATE SKIP LOCKED`, and runs the handler. Started with `soniq worker`. Multiple workers can compete for the same queue safely.

## Scheduler

A separate long-running process (`soniq scheduler`) that evaluates `@app.periodic` jobs and writes due instances into `soniq_jobs` for workers to pick up. The CLI worker does not evaluate recurring jobs - that is intentional, so worker scaling does not duplicate scheduler work. Multiple scheduler instances coordinate via a Postgres advisory lock; only one does work at a time.

## Queue

A `varchar` column on `soniq_jobs` used for routing. Workers can be started with `--queues=foo,bar` to consume from a subset. Default queue name is `"default"`. Queues are not separate tables - just a routing tag.

## Concurrency

The number of in-flight jobs a single worker process will run at once. Default is 4. Tune up for I/O-bound workloads, down for CPU-bound. Set with `--concurrency` or `SONIQ_WORKER_CONCURRENCY`.

## Heartbeat

Periodic write each worker performs to `soniq_workers` to advertise liveness. If a worker stops heartbeating for `SONIQ_HEARTBEAT_TIMEOUT` seconds (default 300), surviving workers detect it as stale, reset its in-flight jobs to `queued`, and remove its row.

## Dead-letter queue (DLQ)

The `soniq_dead_letter_jobs` table. Jobs that exhaust all retries land here instead of staying in `soniq_jobs`. Use `app.dead_letter.list()`, `app.dead_letter.replay(job_id)`, or the `soniq dead-letter` CLI to inspect and recover them.

## Replay

Re-running a dead-letter job by minting a fresh `soniq_jobs` row from it. The original DLQ row stays put with `resurrection_count` incremented; replay does not delete the dead-letter record.

## At-least-once delivery

Soniq's delivery guarantee. A job will run at least once but may run more than once if a worker crashes mid-handler before marking the row done. Handlers must be **idempotent** - rerunning them must produce the same end state. Soniq does not offer an exactly-once mode; nothing on Postgres alone can.

## Transactional enqueue

Enqueueing a job inside the same Postgres transaction as your business writes by passing `connection=conn` to `enqueue()`. If the transaction rolls back, the job row is also rolled back. The reason most teams pick a Postgres-backed queue. Postgres-only - SQLite and the in-memory backend do not support it.

## Backend

The storage layer. The production backend is `asyncpg` against PostgreSQL. SQLite and an in-memory backend exist for local development and tests; both have hard limitations (single-writer, no transactional enqueue, polling-only) and are not for production.
