# Transactional Enqueue

Enqueue a job inside a database transaction. If the transaction rolls back, the job never enters the queue.

## How it works

When you pass `connection=conn` to `enqueue()`, the job row is inserted into `soniq_jobs` using that connection. The INSERT is part of your transaction, so the job only becomes visible to workers after `COMMIT`.

Either both your business data and the job are committed, or neither is.

<a id="pattern-3-sqlalchemy-async"></a>

## Pattern 1: Pass your SQLAlchemy session

If your app uses SQLAlchemy async, pass the `AsyncSession` as `connection=`. Soniq writes the job on the same connection, inside the same transaction:

```python
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

- **Use the asyncpg driver.** Create the engine with a `postgresql+asyncpg://` URL. Other drivers raise a `SoniqError`. Support for psycopg is planned.
- **Open a transaction first.** Call `enqueue()` inside `async with db.begin():` or after a statement that started one. A session with no active transaction raises a `SoniqError`.
- **SQLAlchemy stays in charge.** Soniq does not open or commit a transaction. If SQLAlchemy rolls back, the job is removed too.
- **Use the same database.** The engine must point at the database that holds the Soniq tables.

Soniq does not import SQLAlchemy unless you pass a SQLAlchemy object.

## Raw asyncpg connections (still supported)

You can also pass any raw `asyncpg` connection inside an active transaction. It can come from Soniq's pool or from your own pool. The patterns below still work.

## Pattern 2: Raw asyncpg via Soniq's pool

The simplest pattern. Borrow a connection from Soniq's own backend pool. No second pool, no extraction step:

```python
await soniq.ensure_initialized()
async with soniq.backend.acquire() as conn:
    async with conn.transaction():
        await conn.execute("INSERT INTO orders (id, total) VALUES ($1, $2)", order_id, total)
        await soniq.enqueue(send_invoice, connection=conn, order_id=order_id)
```

The `connection=conn` parameter is the only thing that changes from a normal enqueue call. Everything else works the same -- job options, priority, scheduling.

## FastAPI route example

A real-world order creation endpoint where the order record and the follow-up job are committed atomically:

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


@soniq.job(queue="invoices", retries=5)
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

If your app already manages its own `asyncpg.Pool` (common in apps that predate Soniq), you don't need to involve Soniq's pool at all. Pass the connection from *your* pool to `enqueue` and the job row goes through your transaction:

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

This is the cleanest pattern when you have an existing pool: there is no extraction step and no second pool to size. Soniq is a passenger on a connection your app already owned.

> Both pools must point at the **same Postgres database**. Soniq's job tables and your application tables share one database; the connection just has to be in a transaction on that database.

## Pattern 4: Tortoise ORM (still supported)

Tortoise exposes the raw `asyncpg` connection on the transaction context, but only via a private attribute. It works today; treat it as a soft API.

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
        # conn._connection is the raw asyncpg connection. Private attribute,
        # works in current Tortoise releases. A first-class Tortoise integration
        # is on the roadmap.
        await soniq.enqueue(send_invoice, connection=conn._connection, order_id=order.id)
```

If you'd like first-class Tortoise support that does not depend on a private attribute, please open an issue -- it's not a hard integration to write, we just want to gauge demand before adding the dependency.

## Use cases

**Order + invoice.** Create the order and enqueue the invoice generation in one transaction. No orphaned orders without invoices.

**User signup + welcome email.** Insert the user row and enqueue the welcome email together. If the INSERT hits a unique constraint, no phantom email gets sent.

**Payment + receipt.** Record the payment and enqueue the receipt delivery atomically. No "payment recorded but receipt never sent" bugs.

The common thread: any workflow where "row exists but job is missing" would be a data integrity bug.

## Delivery semantics

Transactional enqueue guarantees the job enters the queue if and only if the transaction commits. It does not guarantee single execution.

Soniq provides **at-least-once delivery**. If a worker crashes after executing the job but before marking it done, stale worker recovery will re-queue it. Design your job functions to be idempotent -- use upserts, deduplication keys, or check-before-act patterns for side effects like sending emails or charging payments.

## What transactional enqueue does NOT guarantee

- **Single execution.** The guarantee applies to enqueue only. Re-execution after worker crashes is still possible.
- **Rollback after commit.** Once committed, the job is in the queue. You can cancel it with `soniq.cancel_job(job_id)`, but a fast worker might pick it up first.

> **Note:** Transactional enqueue requires PostgreSQL. It is not available with the SQLite or Memory backends. Calling `enqueue(..., connection=conn)` on a non-PostgreSQL backend raises a `ValueError`.
