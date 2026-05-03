# 6. Transactional enqueue

> **Intermediate** - 10 minutes. Atomic enqueue inside your business writes.

The reason most teams pick a Postgres-backed queue.

## The problem

Most queues live outside your database. When your handler does this:

```python
async def create_order(items):
    order_id = await db.insert_order(items)
    await queue.enqueue(send_invoice, order_id=order_id)
    return order_id
```

four things can go wrong:

1. The DB write succeeds, the enqueue fails. No invoice ever goes out.
2. The enqueue succeeds, the DB write fails. The worker picks up the job, looks for an order that does not exist, and crashes.
3. The request rolls back after the enqueue. Same as 2.
4. The process dies between the two calls. Either of 1 or 2.

The standard fix is the [outbox pattern](https://microservices.io/patterns/data/transactional-outbox.html): write to your own table, then have a separate process drain it into the queue. It works, but it is a process to run, monitor, and reason about.

## The Soniq version

Soniq's job table lives in your Postgres. Pass the same connection you are using for your business writes, and the job row joins your transaction:

```python
async with app.backend.acquire() as conn:
    async with conn.transaction():
        order_id = await conn.fetchval(
            "INSERT INTO orders (items) VALUES ($1) RETURNING id",
            items,
        )
        await app.enqueue(send_invoice, connection=conn, order_id=order_id)
```

If the transaction commits, both the order and the job exist. If it rolls back, neither does. There is no window where one is real and the other is not.

This closes the producer-side hole. The worker side is still at-least-once (a worker can crash after running your handler but before marking the row done, and the heartbeat sweep will requeue it), so your handler still needs to be idempotent. But the order-and-job-go-out-of-sync class of bugs is gone.

## Where to go next

The [transactional enqueue guide](../guides/transactional-enqueue.md) covers the four real-world shapes:

- Raw `asyncpg` with Soniq's pool (simplest)
- Raw `asyncpg` with your own pool
- SQLAlchemy async sessions (the FastAPI shape)
- Tortoise ORM

If you are coming from Celery or RQ, this is the feature that does not exist over there. Read the guide, pick the pattern that matches your stack, and you are done.
