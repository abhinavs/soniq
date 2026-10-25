---
description: Tune PostgreSQL for Soniq - connection pool sizing, PgBouncer session versus transaction mode, LISTEN/NOTIFY and server settings for a Python job queue.
---

# Tune PostgreSQL for Soniq workers

Soniq runs on PostgreSQL in production. This page covers connection pool sizing, PgBouncer, and LISTEN/NOTIFY behavior.

## Connection pool settings

Soniq maintains its own async connection pool (via asyncpg). Three settings control it:

| Variable | Default | Description |
|----------|---------|-------------|
| `SONIQ_POOL_MIN_SIZE` | `5` | Minimum connections kept open. |
| `SONIQ_POOL_MAX_SIZE` | `20` | Maximum connections the pool will open. |
| `SONIQ_POOL_HEADROOM` | `2` | Extra connections reserved beyond worker concurrency for the LISTEN/NOTIFY listener and heartbeat writer. |

## Pool sizing formula

```
pool_max_size >= concurrency + headroom
```

Soniq uses connections for:

| Purpose | Connections | Lifetime |
|---------|-------------|----------|
| Job processing | Up to `concurrency` | Short (per job) |
| LISTEN/NOTIFY listener | 1 | Long-lived |
| Worker heartbeat | 1 | Periodic |
| Cleanup / scheduler | 1 (shared) | Periodic |

**Total per worker process** = `concurrency + SONIQ_POOL_HEADROOM` (default headroom is 2)

With the default concurrency of 4, budget 6 connections per worker process. If you run 3 worker processes at concurrency 4, you need 18 connections in total.

Set `pool_max_size` accordingly. If your pool is too small, workers will block waiting for a connection. If it's too large, you waste PostgreSQL backend slots.

> **Warning:** Each PostgreSQL connection consumes about 5-10 MB of server memory. If you run many workers, watch `max_connections` on the PostgreSQL side.

### Example: 3 workers, concurrency 8

```bash
# Per worker: 8 (jobs) + 2 (headroom) = 10 connections
# Start each worker with: soniq worker myapp.jobs:soniq --concurrency 8
export SONIQ_POOL_MIN_SIZE=5
export SONIQ_POOL_MAX_SIZE=12
export SONIQ_POOL_HEADROOM=2

# PostgreSQL side: 3 workers * 12 = 36 connections needed
# Set max_connections >= 50 (leave room for admin/monitoring)
```

## PgBouncer

If you run PgBouncer between Soniq and PostgreSQL, the pooling mode matters a lot.

### Session mode works

In session mode, PgBouncer assigns a server connection for the lifetime of a client connection. This is fully compatible with Soniq because `LISTEN/NOTIFY` requires a persistent connection.

```ini
[pgbouncer]
pool_mode = session
```

No special configuration needed on the Soniq side.

### Transaction mode breaks LISTEN/NOTIFY

In transaction mode, PgBouncer returns the server connection to the pool after each transaction. `LISTEN` subscriptions are lost between transactions, and `NOTIFY` messages won't reach the intended listener.

**In transaction mode, Soniq does not receive instant job notifications.** It falls back to polling, which adds latency (up to `SONIQ_POLL_INTERVAL` seconds, default 5).

If you must use transaction mode:

- Accept the added latency from polling. The scheduler also needs session pooling, because its leader lock is a session-scoped advisory lock.
- Or connect Soniq directly to PostgreSQL, bypassing PgBouncer, and route your application traffic through PgBouncer. This is the recommended approach.

### Connection count math with PgBouncer

With the default concurrency of 4, budget 6 connections per worker. If you run 3 workers at concurrency 4, you need 18 connections.

Set PgBouncer's `max_client_conn` to accommodate your total Soniq connections plus your application's connections. Keep `default_pool_size` >= your total Soniq connection count.

### Recommendation

For most setups, point Soniq directly at PostgreSQL and use PgBouncer for your application's read-heavy queries. This sidesteps the LISTEN/NOTIFY limitation entirely and keeps things simple.

## LISTEN/NOTIFY considerations

Soniq uses PostgreSQL `LISTEN/NOTIFY` for instant worker wakeup when jobs are enqueued. This is what makes job pickup near-instant rather than polling-based.

Things to know:

- The listener holds one long-lived connection per worker process. This connection cannot go through a connection pooler in transaction mode.
- If the listener connection drops, Soniq falls back to polling and reconnects automatically.
- `NOTIFY` payloads are limited to 8000 bytes in PostgreSQL. Soniq sends only the queue name, so this is never a problem in practice.
- If you use a managed PostgreSQL service (RDS, Cloud SQL, etc.), LISTEN/NOTIFY works out of the box. No special configuration needed.

## PostgreSQL server tuning

For Soniq-heavy workloads, these PostgreSQL settings help:

```
# postgresql.conf
shared_buffers = 256MB          # 25% of available RAM, up to a point
work_mem = 4MB                  # Per-operation memory
max_connections = 200           # Account for all workers + app + admin
```

`soniq setup` creates the indexes that the worker queries need, including indexes on `(queue, status, priority)`, `scheduled_at` and `worker_id`. Do not add duplicates of them. Add your own index only for your own queries on `soniq_jobs`, and create it with `CREATE INDEX CONCURRENTLY` so it does not block the queue.

## Next steps

- [Going to production](going-to-production.md) - the full checklist
- [Reliability](reliability.md) - what happens when the database or a worker fails
