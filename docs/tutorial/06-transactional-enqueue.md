---
description: Enqueue a Soniq job inside your PostgreSQL transaction so the job exists only if the transaction commits. Replaces the outbox pattern for Python background jobs.
---

# 6. Enqueue jobs inside a PostgreSQL transaction

> **Intermediate** - 10 minutes. Atomic enqueue inside your business writes.

Transactional enqueue adds a Soniq job in the same PostgreSQL transaction as your business write. If the transaction commits, both exist. If it rolls back, neither exists. Use it when a job depends on data that you write in the same request.

## The problem

Most queues live outside your database. Consider this handler:

```python
async def create_order(items):
    order_id = await db.insert_order(items)
    await queue.enqueue(send_invoice, order_id=order_id)
    return order_id
```

Four failures can happen:

1. The database write succeeds and the enqueue fails. No invoice is sent.
2. The enqueue succeeds and the database write fails. The worker claims the job, looks for an order that does not exist, and crashes.
3. The request rolls back after the enqueue. The result is the same as failure 2.
4. The process dies between the two calls. The result is failure 1 or failure 2.

The standard fix is the [outbox pattern](https://microservices.io/patterns/data/transactional-outbox.html). You write to your own table, and a separate process moves the rows into the queue. This works, but you must run and monitor that extra process.

## The Soniq version

Soniq's job table is in your PostgreSQL database. Pass the connection that you use for your business writes, and the job row joins your transaction:

```python
await soniq.ensure_initialized()   # soniq.backend is None until this runs
async with soniq.backend.acquire() as conn:
    async with conn.transaction():
        order_id = await conn.fetchval(
            "INSERT INTO orders (items) VALUES ($1) RETURNING id",
            items,
        )
        await soniq.enqueue(send_invoice, connection=conn, order_id=order_id)
```

If the transaction commits, the order and the job both exist. If it rolls back, neither exists. At no point does one exist without the other.

This fixes the producer side. The worker side is still at-least-once. A worker can crash after it runs your handler but before it marks the row done. The stale-worker sweep then requeues the job. Your handler must therefore be idempotent. The order and the job can no longer go out of sync.

## Next steps

The [transactional enqueue guide](../guides/transactional-enqueue.md) covers four setups:

- Raw `asyncpg` with Soniq's pool (simplest)
- Raw `asyncpg` with your own pool
- SQLAlchemy async sessions (the FastAPI shape)
- Tortoise ORM

Celery and RQ do not have this feature. Read the guide and pick the pattern that matches your stack.

You have finished the tutorial. To prepare a deployment, read [going to production](../production/going-to-production.md).
