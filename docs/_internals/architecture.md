# Architecture

A short tour of how Soniq actually works under the hood. Reading this isn't required, but if you understand the model, you'll trust the library more, debug it faster, and feel comfortable answering "is this the right tool?" without guessing.

The whole design fits in five ideas: jobs are rows, workers compete via `SKIP LOCKED`, pickup is push-based via `LISTEN/NOTIFY`, liveness is tracked via heartbeats, and transactional enqueue is just an `INSERT` on a connection you already own.

## Jobs are rows

Soniq stores jobs in a table called `soniq_jobs`. When you call `await app.enqueue(send_welcome, to="dev@example.com")`, Soniq does an `INSERT`. There is no broker, no queue process, no in-memory data structure that needs to survive a restart. The queue *is* the table.

A simplified view of the schema (the real definition is in `soniq/backends/postgres/migrations/0001_core.sql`):

```sql
CREATE TABLE soniq_jobs (
  id              UUID PRIMARY KEY,
  name            TEXT NOT NULL,           -- the registered task name
  queue           TEXT NOT NULL DEFAULT 'default',
  status          TEXT NOT NULL,           -- 'queued' | 'processing' | 'done' | ...
  args            JSONB NOT NULL,
  priority        INTEGER NOT NULL,
  scheduled_at    TIMESTAMPTZ NOT NULL,
  attempts        INTEGER NOT NULL DEFAULT 0,
  max_retries     INTEGER NOT NULL,
  last_error      TEXT,
  worker_id       TEXT,
  ...
);
```

A job's lifetime is just the lifetime of a row. Querying the queue is a `SELECT`. Backing up the queue is whatever your Postgres backup story already does. Auditing what happened is `SELECT * FROM soniq_jobs WHERE id = ...`.

## Workers compete with `SELECT ... FOR UPDATE SKIP LOCKED`

When a worker is ready to process, it runs a query roughly like this:

```sql
SELECT id, name, args, ...
FROM soniq_jobs
WHERE status = 'queued'
  AND scheduled_at <= now()
  AND queue = ANY($1)
ORDER BY priority ASC, scheduled_at ASC
LIMIT 1
FOR UPDATE SKIP LOCKED;
```

`FOR UPDATE` takes a row-level lock. `SKIP LOCKED` tells Postgres to skip rows another transaction has already locked. Multiple workers can run this query concurrently and each will get a *different* row, with no blocking and no application-level coordination.

If the row is found, the same transaction updates it to `status = 'processing'`, stamps the worker id, and commits. The handler then runs outside that transaction. When it finishes, a second statement updates the row to `done` (or `failed`/`dead_letter`).

This is the Postgres-native version of "exactly one worker claims a job." The classical alternative is an advisory lock or polling with retries; `SKIP LOCKED` is faster and simpler.

## Pickup is push-based via `LISTEN/NOTIFY`

If workers only checked the table on a poll loop, pickup latency would equal the poll interval (default 5 seconds). That's fine for batch workloads but bad for user-facing jobs.

Postgres has `LISTEN/NOTIFY`, a built-in pub/sub channel. When a job is enqueued, Soniq emits:

```sql
NOTIFY soniq_new_job, '<queue-name>';
```

Every worker subscribes (`LISTEN soniq_new_job`) on a dedicated connection. When a NOTIFY arrives, the worker wakes up immediately and runs the claim query above. Pickup latency is typically under 10 ms, even at low concurrency, with no polling overhead.

If `LISTEN/NOTIFY` is unavailable (e.g. PgBouncer transaction-pooling mode), Soniq falls back to polling at `SONIQ_POLL_INTERVAL`. Things still work, just with higher pickup latency.

## Liveness is tracked via heartbeats

Each worker registers itself in a `soniq_workers` table on startup and updates a `last_heartbeat` timestamp every `SONIQ_HEARTBEAT_INTERVAL` seconds (default 5).

If a worker crashes hard (`SIGKILL`, OOM, hardware failure), it cannot run its shutdown logic. Its in-flight jobs would be stuck in `processing` forever. To handle that, every running worker periodically scans for peers whose `last_heartbeat` is older than `SONIQ_HEARTBEAT_TIMEOUT` (default 300 seconds), marks them dead, and resets their `processing` jobs back to `queued`.

This is also why Soniq's at-least-once guarantee exists: if a worker dies after running your handler but before updating the status to `done`, the heartbeat sweep will eventually requeue the job and another worker will run it. Idempotent handlers absorb that gracefully; non-idempotent ones don't, which is why the docs hammer on idempotency so much.

## Transactional enqueue is just an INSERT on your connection

When you pass `connection=conn` to `enqueue()`, Soniq doesn't open its own transaction. It runs the `INSERT INTO soniq_jobs ...` statement on the connection you handed it, inside whatever transaction that connection is already running.

That means Postgres' visibility rules apply automatically:

- If your transaction commits, the job row is visible to the next worker that runs the claim query.
- If your transaction rolls back, the job row never existed.

There is no separate "queue transaction" to coordinate with your "business transaction" -- it's the same transaction. The job and your business write are atomically committed or atomically discarded.

This is the property that distinguishes Soniq from broker-based queues. A broker is on the other side of a network and a TCP connection from your database. The two cannot share a transaction. Soniq's queue and your data are the same Postgres database, so they share whatever your code chooses to put inside `BEGIN ... COMMIT`.

See the [transactional enqueue guide](../guides/transactional-enqueue.md) for the four code patterns that make this work with raw asyncpg, your own pool, SQLAlchemy, and Tortoise.

## End-to-end lifecycle

Putting all of it together:

```
+--------------------+         INSERT row, NOTIFY        +--------------------+
| Producer (your app)| --------------------------------> | Postgres            |
+--------------------+                                    | soniq_jobs (queued) |
                                                         +----------+----------+
                                                                    |
                                                          NOTIFY    |
                                                          soniq_new_job
                                                                    v
                                                         +--------------------+
                                                         | Worker             |
                                                         | LISTEN -> wake     |
                                                         | SELECT FOR UPDATE  |
                                                         | SKIP LOCKED        |
                                                         | UPDATE -> processing
                                                         +----------+---------+
                                                                    |
                                                          run handler
                                                                    |
                                                                    v
                                                         +--------------------+
                                                         | UPDATE -> done     |
                                                         | (or failed,        |
                                                         |  retry, or         |
                                                         |  dead_letter)      |
                                                         +--------------------+
```

Every arrow is a single SQL statement. There is no other state. That's the whole library.

## What this design implies

- **Backups, point-in-time recovery, replication** all work for jobs because jobs are just rows.
- **Migrations** work the same way they do for any Postgres table -- `soniq setup` runs versioned SQL migrations from `soniq/backends/postgres/migrations/`.
- **Throughput is bounded by Postgres write throughput.** It scales to thousands of jobs/sec on commodity hardware, which is enough for almost all web applications. If you need 10k+ sustained, see [When NOT to use Soniq](../index.md#when-not-to-use-soniq).
- **Latency is bounded by `LISTEN/NOTIFY` round-trip plus the claim query.** Typically under 10 ms.
- **Failure modes are Postgres failure modes.** If your database is up, the queue is up. If the database is down, you have bigger problems and a separate Redis wouldn't have helped.

## See also

- [Workers](workers.md) -- runtime behaviour, heartbeats, graceful shutdown
- [Queues](queues.md) -- queue semantics and worker scoping
- [Dead-letter queue](dead-letter.md) -- the `soniq_dead_letter_jobs` table
- [Transactional enqueue](../guides/transactional-enqueue.md) -- four code patterns for the headline feature
