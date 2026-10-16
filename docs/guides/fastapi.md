# FastAPI Integration

Soniq's Instance API fits FastAPI well. You get explicit lifecycle control, clean dependency injection, and easy testing.

## Start with one process

The simplest setup runs the API and the worker in one process. You need no second service.

```python
# app/main.py
from fastapi import FastAPI
from soniq import Soniq
from soniq.integrations.fastapi import lifespan

soniq = Soniq(database_url="postgresql://localhost/myapp")

app = FastAPI(lifespan=lifespan(soniq, run_worker=True, concurrency=2))


@soniq.job(queue="emails", retries=3)
async def send_welcome(user_id: int):
    user = await get_user(user_id)
    await send_email(to=user.email, template="welcome")


@app.post("/users")
async def create_user(name: str, email: str):
    user = await save_user(name=name, email=email)
    await soniq.enqueue(send_welcome, user_id=user.id)
    return {"id": user.id, "queued": True}
```

Run it with `uvicorn app.main:app`. That is all.

`lifespan()` takes these arguments:

| Argument | Default | Meaning |
|---|---|---|
| `run_worker` | `False` | Start a worker in the same event loop as the API. |
| `concurrency` | `2` | Number of jobs the worker runs at the same time. |
| `queues` | `None` | Queue names for the worker. `None` means all queues. |
| `extra` | `None` | Your own lifespan. See [Chain your own lifespan](#chain-your-own-lifespan). |

On startup, the helper initializes the connection pool and starts the worker. On shutdown, it stops the worker and closes the pool.

The worker stops in the normal graceful way. It stops taking new jobs and waits for running jobs to finish. If a job runs longer than `shutdown_timeout` (30 seconds by default), Soniq cancels it and puts it back in the queue. No job is lost. See the [shutdown contract](../_internals/contracts/shutdown.md) for the full rules.

Handlers can run again after a crash, so write them to be idempotent.

!!! warning "Run migrations at deploy time, not app startup"
    Use `soniq setup` in your deploy pipeline (CI step, Dockerfile entrypoint, k8s init container). Do not run it in the lifespan. Running migrations on every boot creates race conditions when many replicas start at the same time.

!!! warning "One Uvicorn process only"
    With `run_worker=True`, each Uvicorn worker process starts its own Soniq worker. If you run `uvicorn --workers 4`, or Gunicorn with many workers, you get four Soniq workers with the `concurrency` you set. This is safe, because Soniq workers never claim the same job twice. But the total number of jobs that run at once is `processes x concurrency`. The database pool also grows with each process. Set `concurrency` with this in mind, or move the worker to its own process (next section).

Also keep CPU-heavy jobs out of the API process. A job that blocks the event loop slows down your HTTP requests.

### Chain your own lifespan

Pass `extra` if you already have a lifespan. It takes the app and returns an async context manager:

```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def my_lifespan(app: FastAPI):
    app.state.http = make_http_client()
    yield
    await app.state.http.aclose()

app = FastAPI(lifespan=lifespan(soniq, run_worker=True, extra=my_lifespan))
```

Soniq is ready before `my_lifespan` starts. `my_lifespan` stops before the worker stops.

## Move the worker to its own process

When the load grows, run the worker as a separate process. Remove `run_worker=True`. The API keeps the same lifespan, so the pool still opens and closes correctly:

```python
app = FastAPI(lifespan=lifespan(soniq))
```

Start the worker with the CLI. Point it at the module where your jobs are defined:

```bash
SONIQ_DATABASE_URL="postgresql://localhost/myapp" \
SONIQ_JOBS_MODULES="app.main" \
soniq worker --concurrency 4
```

`SONIQ_JOBS_MODULES` is a comma-separated list of Python modules that the worker imports on startup. This is how it finds all `@soniq.job()` decorators. See [Job module discovery](../getting-started/installation.md#job-module-discovery) for cross-service setups and per-worker overrides.

You can limit a worker to some queues:

```bash
soniq worker --concurrency 2 --queues emails,notifications
```

Run the API and the worker as separate processes:

```bash
# Terminal 1: API server
uvicorn app.main:app --reload

# Terminal 2: Worker
SONIQ_DATABASE_URL="postgresql://localhost/myapp" \
SONIQ_JOBS_MODULES="app.main" \
soniq worker --concurrency 4
```

Your job code and your `enqueue()` calls do not change.

## Coming from BackgroundTasks

FastAPI's `BackgroundTasks` runs a function after the response is sent, in the same process. If the process stops, the task is lost. It has no retries and no queue.

With Soniq, the job is a row in PostgreSQL. It survives a restart and it retries on failure.

```diff
-from fastapi import BackgroundTasks, FastAPI
+from fastapi import FastAPI
+from soniq import Soniq
+from soniq.integrations.fastapi import lifespan

-app = FastAPI()
+soniq = Soniq(database_url="postgresql://localhost/myapp")
+app = FastAPI(lifespan=lifespan(soniq, run_worker=True))

+@soniq.job(retries=3)
 async def send_welcome(user_id: int):
     ...

 @app.post("/users")
-async def create_user(name: str, background_tasks: BackgroundTasks):
+async def create_user(name: str):
     user = await save_user(name=name)
-    background_tasks.add_task(send_welcome, user.id)
+    await soniq.enqueue(send_welcome, user_id=user.id)
     return {"id": user.id}
```

Job arguments are keyword-only and must be JSON-serializable.

## Defining jobs

Register jobs with the `@soniq.job()` decorator. Jobs are regular async functions:

```python
@soniq.job(queue="emails", retries=3)
async def send_welcome(user_id: int):
    user = await get_user(user_id)
    await send_email(to=user.email, template="welcome")
```

## Enqueuing from route handlers

Call `soniq.enqueue()` from any route:

```python
@app.post("/orders")
async def create_order(product_id: int, quantity: int):
    order = await save_order(product_id=product_id, quantity=quantity)
    await soniq.enqueue(process_order, order_id=order.id)
    return {"order_id": order.id}
```

## Multiple instances

Each `Soniq` instance is fully isolated. It has its own connection pool and job registry. This is useful for multi-tenant setups. The helper takes one instance, so close the others in your own lifespan:

```python
tenant_a = Soniq(database_url="postgresql://localhost/tenant_a")
tenant_b = Soniq(database_url="postgresql://localhost/tenant_b")

@asynccontextmanager
async def close_tenant_b(app: FastAPI):
    yield
    await tenant_b.close()

app = FastAPI(lifespan=lifespan(tenant_a, extra=close_tenant_b))
```

## Borrowing a connection

For workflows like [transactional enqueue](transactional-enqueue.md), borrow a connection from the backend:

```python
await soniq.ensure_initialized()
async with soniq.backend.acquire() as conn:
    async with conn.transaction():
        await conn.execute("INSERT INTO orders ...")
        await soniq.enqueue(send_invoice, connection=conn, order_id=order_id)
```
