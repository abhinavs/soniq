---
description: Glossary of Soniq terms: job, handler, task name, worker, scheduler, queue, heartbeat, dead-letter queue and at-least-once delivery for Python on PostgreSQL.
---

# Soniq glossary: job, worker, queue and dead-letter terms

This page defines the words Soniq's docs use for its PostgreSQL-backed Python background jobs, in one paragraph each.

## Job

A unit of work. Either the function you decorate with `@soniq.job()` (the **handler**), or a specific row in `soniq_jobs` representing one scheduled invocation of that function (the **job instance**). Most of the docs say "job" for both and the meaning is clear from context. When ambiguity matters, "handler" and "job row" are the disambiguating terms.

## Handler

The Python function that runs when a job is processed. Registered with `@soniq.job()`.

## Task name

The string identifier under which a handler is registered. Set it with `@soniq.job(name="...")`. If you omit `name=`, Soniq derives `f"{module}.{qualname}"`, which is deprecated since 0.2.0 and warns. The task name is the wire protocol for cross-service deployments. Producers refer to handlers by name, not by import.

## TaskRef

A typed stub that lets a producer enqueue a job by name without importing the consumer's code. Created via `task_ref(name="billing.send_invoice", args_model=InvoiceArgs)` - the arguments are keyword-only. Use it when the producer must not own the handler code, as in a cross-service setup. See [cross-service jobs](../guides/cross-service-jobs.md).

## Worker

A long-running process that polls for jobs (or receives a PostgreSQL `NOTIFY`), claims them with `SELECT ... FOR UPDATE SKIP LOCKED`, and runs the handler. Start it with `soniq worker`. Several workers can safely take jobs from the same queue.

## Scheduler

A separate long-running process (`soniq scheduler`) that evaluates `@soniq.periodic` jobs and writes due instances into `soniq_jobs` for workers to pick up. The worker does not evaluate recurring jobs. This is intentional: scaling workers does not duplicate scheduler work. Several scheduler instances coordinate with a PostgreSQL advisory lock, and only one does work at a time.

## Queue

A `varchar` column on `soniq_jobs` used for routing. Start a worker with `--queues=foo,bar` to consume from a subset. The default queue name is `"default"`. Queues are not separate tables. A queue is a routing tag.

## Concurrency

The number of jobs a single worker process runs at once. The default is 4. Raise it for I/O-bound workloads and lower it for CPU-bound ones. Set it with `soniq worker --concurrency N`, or with `concurrency=` on `run_worker()`.

## Heartbeat

A periodic write that each worker makes to `soniq_workers` to show it is alive. If a worker stops its heartbeat for `SONIQ_HEARTBEAT_TIMEOUT` seconds (default 300), the surviving workers mark it `stopped` and reset its in-flight jobs to `queued`.

## Dead-letter queue (DLQ)

The `soniq_dead_letter_jobs` table. Jobs that exhaust all retries land here instead of staying in `soniq_jobs`. To inspect and recover them, use `soniq.dead_letter.list_dead_letter_jobs()`, `soniq.dead_letter.replay(dead_letter_id)` or the `soniq dead-letter` CLI. See the [dead-letter Python API](../api/dead-letter.md).

## Replay

Re-running a dead-letter job by minting a fresh `soniq_jobs` row from it. The original DLQ row stays put with `resurrection_count` incremented; replay does not delete the dead-letter record.

## At-least-once delivery

Soniq's delivery guarantee. A job runs at least once. It can run more than once if a worker crashes in the handler before it marks the row done. Handlers must be **idempotent**: running them again must produce the same end state. Soniq has no exactly-once mode, because PostgreSQL alone cannot guarantee it.

## Transactional enqueue

Enqueueing a job inside the same Postgres transaction as your business writes by passing `connection=conn` to `enqueue()`. If the transaction rolls back, the job row is also rolled back. It is the main reason to pick a PostgreSQL-backed queue. It works on PostgreSQL only - SQLite and the in-memory backend do not support it.

## Backend

The storage layer. The production backend is `asyncpg` against PostgreSQL. SQLite and an in-memory backend exist for local development and tests; both have hard limits (single writer, no transactional enqueue, polling only) and are not for production.
