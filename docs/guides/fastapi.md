# FastAPI Integration

Soniq's Instance API is the natural fit for FastAPI applications. You get explicit lifecycle control, clean dependency injection, and easy testing.

## Setup

Create an `Soniq` instance and wire it into FastAPI's lifespan:

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from soniq import Soniq

eq = Soniq(database_url="postgresql://localhost/myapp")

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await eq.close()     # closes the connection pool

app = FastAPI(lifespan=lifespan)
```

The connection pool initializes lazily on first use (first `enqueue()` call). `close()` shuts it down cleanly when the process exits.

!!! warning "Run migrations at deploy time, not app startup"
    Use `soniq setup` in your deploy pipeline (CI step, Dockerfile entrypoint, k8s init container) — not in the lifespan. Running migrations on every app boot creates race conditions when multiple replicas start simultaneously.

## Defining jobs

Register jobs with the `@eq.job()` decorator. These are regular async functions:

```python
@eq.job(queue="emails", max_retries=3)
async def send_welcome(user_id: int):
    user = await get_user(user_id)
    await send_email(to=user.email, template="welcome")
```

## Enqueuing from route handlers

Call `eq.enqueue()` from any route:

```python
@app.post("/users")
async def create_user(name: str, email: str):
    user = await save_user(name=name, email=email)
    await eq.enqueue(send_welcome, user_id=user.id)
    return {"id": user.id, "queued": True}
```

## Running the worker

Workers run as a separate process. Point them at the module where your jobs are defined:

```bash
SONIQ_DATABASE_URL="postgresql://localhost/myapp" \
SONIQ_JOBS_MODULES="app.jobs" \
soniq worker --concurrency 4
```

`SONIQ_JOBS_MODULES` is a comma-separated list of Python modules the worker imports on startup so it discovers all `@eq.job()` decorators. See [Job module discovery](../getting-started/installation.md#job-module-discovery) for cross-service setups and per-worker overrides.

You can also limit a worker to specific queues:

```bash
soniq worker --concurrency 2 --queues emails,notifications
```

## Complete example

```python
# app/main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI
from soniq import Soniq

eq = Soniq(database_url="postgresql://localhost/myapp")

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await eq.close()

app = FastAPI(lifespan=lifespan)


@eq.job(queue="emails", max_retries=3, retry_delay=30)
async def send_welcome(user_id: int):
    user = await get_user(user_id)
    await send_email(to=user.email, template="welcome")


@eq.job(queue="default")
async def process_order(order_id: int):
    order = await get_order(order_id)
    await fulfill(order)


@app.post("/users")
async def create_user(name: str, email: str):
    user = await save_user(name=name, email=email)
    await eq.enqueue(send_welcome, user_id=user.id)
    return {"id": user.id}


@app.post("/orders")
async def create_order(product_id: int, quantity: int):
    order = await save_order(product_id=product_id, quantity=quantity)
    await eq.enqueue(process_order, order_id=order.id)
    return {"order_id": order.id}
```

Run the API and worker separately:

```bash
# Terminal 1: API server
uvicorn app.main:app --reload

# Terminal 2: Worker
SONIQ_DATABASE_URL="postgresql://localhost/myapp" \
SONIQ_JOBS_MODULES="app.main" \
soniq worker --concurrency 4
```

## Multiple instances

Each `Soniq` instance is fully isolated with its own connection pool and job registry. This is useful for multi-tenant setups:

```python
tenant_a = Soniq(database_url="postgresql://localhost/tenant_a")
tenant_b = Soniq(database_url="postgresql://localhost/tenant_b")

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await tenant_a.close()
    await tenant_b.close()
```

## Borrowing a connection

For workflows like [transactional enqueue](transactional-enqueue.md), borrow
a connection from the backend:

```python
await eq.ensure_initialized()
async with eq.backend.acquire() as conn:
    async with conn.transaction():
        await conn.execute("INSERT INTO orders ...")
        await eq.enqueue(send_invoice, connection=conn, order_id=order_id)
```
