---
description: Enqueue Soniq background jobs inside a PostgreSQL transaction with SQLAlchemy, asyncpg or psycopg 3. If the transaction rolls back, the job is never queued.
---

# Transactional enqueue: add a job in the same PostgreSQL transaction

Soniq can write a job row on the same PostgreSQL connection as your business data. If your transaction rolls back, the job rolls back too. This works with SQLAlchemy async, raw asyncpg, and psycopg 3.

## How it works

When you pass `connection=conn` to `enqueue()`, Soniq inserts the job row into `soniq_jobs` on that connection. The INSERT is part of your transaction, so workers see the job only after `COMMIT`. Your business data and the job are either both committed or both rolled back.

<a id="pattern-3-sqlalchemy-async"></a>

## Pattern 1: Pass your SQLAlchemy session

If your app uses SQLAlchemy async, pass the `AsyncSession` as `connection=`. Soniq writes the job on the same connection, in the same transaction:

```python
import os

from fastapi import Depends, FastAPI
from sqlalchemy.ext.asyncio import AsyncSession
from soniq import Soniq

app = FastAPI()
soniq = Soniq(database_url=os.environ["DATABASE_URL"])

@app.post("/orders")
async def create_order(
    product_id: int,
    quantity: int,
    db: AsyncSession = Depends(get_db),
):
    async with db.begin():
        order = Order(product_id=product_id, quantity=quantity)
        db.add(order)
        await db.flush()                      # populate order.id without committing
        await soniq.enqueue(send_invoice, connection=db, order_id=order.id)
    # SQLAlchemy commits here. Workers see the job at the same moment.
    return {"order_id": order.id}
```

An `AsyncConnection` works too:

```python
async with engine.begin() as conn:
    await conn.execute(insert(orders).values(id=order_id, total=total))
    await soniq.enqueue(send_invoice, connection=conn, order_id=order_id)
```

Rules:

- **Use the asyncpg or psycopg 3 driver.** Create the engine with a `postgresql+asyncpg://` or `postgresql+psycopg://` URL. Other drivers raise a `SoniqError`.
- **Open a transaction first.** Call `enqueue()` inside `async with db.begin():` or after a statement that started one. A session with no active transaction raises a `SoniqError`.
- **SQLAlchemy stays in charge.** Soniq does not open or commit a transaction. If SQLAlchemy rolls back, the job is removed too.
- **Use the same database.** The engine must point at the database that holds the Soniq tables.

Soniq does not import SQLAlchemy unless you pass a SQLAlchemy object.

## Raw asyncpg connections

You can also pass a raw `asyncpg` connection. It can come from Soniq's pool or from your own pool. The connection must be inside an active transaction. Without one, `enqueue()` raises a `SoniqError` with code `SONIQ_UNSUPPORTED_CONNECTION`.

## Pattern 2: Raw asyncpg via Soniq's pool

Borrow a connection from Soniq's own backend pool. You need no second pool:

```python
await soniq.ensure_initialized()
async with soniq.backend.acquire() as conn:
    async with conn.transaction():
        await conn.execute("INSERT INTO orders (id, total) VALUES ($1, $2)", order_id, total)
        await soniq.enqueue(send_invoice, connection=conn, order_id=order_id)
```

The `connection=conn` parameter is the only thing that changes from a normal enqueue call. Job options, priority and scheduling work the same way.

## FastAPI route example

This order endpoint commits the order record and the follow-up job in one transaction:

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from soniq import Soniq

soniq = Soniq(database_url="postgresql://localhost/myapp")

@asynccontextmanager
async def lifespan(app: FastAPI):
    await soniq.ensure_initialized()
    yield
    await soniq.close()

app = FastAPI(lifespan=lifespan)


@soniq.job(name="invoices.send_invoice", queue="invoices", retries=5)
async def send_invoice(order_id: int):
    order = await get_order(order_id)
    await generate_and_send_invoice(order)


@app.post("/orders")
async def create_order(product_id: int, quantity: int):
    async with soniq.backend.acquire() as conn:
        async with conn.transaction():
            order_id = await conn.fetchval(
                "INSERT INTO orders (product_id, quantity) VALUES ($1, $2) RETURNING id",
                product_id, quantity,
            )
            await soniq.enqueue(send_invoice, connection=conn, order_id=order_id)

    return {"order_id": order_id}
```

If the INSERT fails or anything else raises inside the transaction block, both the order row and the job are rolled back.

## Pattern 3: Bring your own asyncpg pool

If your app already manages its own `asyncpg.Pool`, you do not need Soniq's pool. Pass the connection from your pool to `enqueue()` and the job row goes through your transaction:

```python
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI

soniq = Soniq(database_url=os.environ["DATABASE_URL"])
db_pool: asyncpg.Pool                                # your application's pool

@asynccontextmanager
async def lifespan(app: FastAPI):
    global db_pool
    db_pool = await asyncpg.create_pool(os.environ["DATABASE_URL"])
    yield
    await db_pool.close()

app = FastAPI(lifespan=lifespan)

@app.post("/orders")
async def create_order(product_id: int, quantity: int):
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            order_id = await conn.fetchval(
                "INSERT INTO orders (product_id, quantity) VALUES ($1, $2) RETURNING id",
                product_id, quantity,
            )
            await soniq.enqueue(send_invoice, connection=conn, order_id=order_id)
    return {"order_id": order_id}
```

With this pattern you have no second pool to size. Soniq uses a connection that your app already owns.

!!! note
    The connection must point at the same PostgreSQL database that holds the Soniq tables.

## psycopg 3 connections

You can pass a `psycopg.AsyncConnection` too. Soniq writes the job on that connection and does not commit. You commit or roll back yourself:

```python
import psycopg

async with await psycopg.AsyncConnection.connect(DATABASE_URL) as conn:
    await conn.execute("INSERT INTO orders (id, total) VALUES (%s, %s)", (order_id, total))
    await soniq.enqueue(send_invoice, connection=conn, order_id=order_id)
    await conn.commit()
```

Install the driver with `pip install "soniq[psycopg]"`.

## Pattern 4: Tortoise ORM

Tortoise exposes the raw `asyncpg` connection on the transaction context only through a private attribute. The attribute works today, but Tortoise can change it in any release.

```python
from tortoise.transactions import in_transaction

@app.post("/orders")
async def create_order(product_id: int, quantity: int):
    async with in_transaction() as conn:
        order = await Order.create(
            using_db=conn,
            product_id=product_id,
            quantity=quantity,
        )
        # conn._connection is the raw asyncpg connection. It is a private
        # Tortoise attribute, so it can break in a Tortoise upgrade.
        await soniq.enqueue(send_invoice, connection=conn._connection, order_id=order.id)
```

Soniq has no Tortoise integration. If you want one that does not depend on a private attribute, open an issue.

## Use cases

**Order and invoice.** Create the order and enqueue the invoice job in one transaction. No order exists without its invoice job.

**User signup and welcome email.** Insert the user row and enqueue the welcome email together. If the INSERT hits a unique constraint, Soniq queues no email.

**Payment and receipt.** Record the payment and enqueue the receipt job in one transaction. A recorded payment always has a receipt job.

Use this pattern when a row without its job would be a data integrity bug.

## Delivery semantics

Transactional enqueue guarantees the job enters the queue if and only if the transaction commits. It does not guarantee single execution.

Soniq provides **at-least-once delivery**. If a worker crashes after it runs the job but before it marks the job done, stale worker recovery puts the job back in the queue. Write idempotent handlers. Use upserts, deduplication keys, or check-before-act patterns for side effects such as sending emails or charging payments.

## What transactional enqueue does NOT guarantee

- **Single execution.** The guarantee applies to enqueue only. Re-execution after worker crashes is still possible.
- **Rollback after commit.** After the commit, the job is in the queue. You can cancel it with `soniq.cancel_job(job_id)`, but a fast worker can claim it first.

!!! note
    Transactional enqueue needs PostgreSQL. With the SQLite or memory backend, `enqueue(..., connection=conn)` and `soniq.backend.acquire()` raise a `ValueError`.

## Next steps

- [FastAPI integration](fastapi.md) - set up the lifespan and the worker
- [Common patterns](common-patterns.md) - deduplication and job results
- [Troubleshooting](../troubleshooting.md) - fix `SONIQ_UNSUPPORTED_CONNECTION` errors

