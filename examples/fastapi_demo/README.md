# FastAPI demo

A small FastAPI app with SQLAlchemy (asyncpg) and Soniq. It shows that a job and your data commit together, or not at all.

| Route | What it does |
|---|---|
| `POST /signup?email=...` | Creates a user and enqueues a welcome email in one transaction. |
| `POST /orders?item=...` | Creates an order and enqueues a job in one transaction. Add `&fail=1` to raise an error and roll both back. |
| `GET /` | Links to the dashboard. |
| `GET /state` | Row counts and job counts, used by the check script. |

The worker runs inside the API process (`lifespan(soniq, run_worker=True)`).

## Run it

You need Docker with Compose.

```bash
cd examples/fastapi_demo
docker compose up --build
BASE_URL=http://localhost:8000 ./demo.sh    # in a second terminal
```

Compose starts Postgres, runs `soniq setup main:soniq` once, then starts the app on port 8000 and the dashboard on port 6161.

## What `demo.sh` checks

1. A signup runs the welcome email job.
2. A good order commits with its job.
3. An order with `fail=1` returns 500. It leaves no order row and no job.

The script prints `OK` and exits 0 when all three hold. Then open <http://localhost:6161>. The dashboard lists the welcome and order jobs as done. It has no entry for the failed order.

## Run it without Docker

```bash
pip install "soniq[dashboard]" "sqlalchemy[asyncio]" fastapi uvicorn
export SONIQ_DATABASE_URL=postgresql://postgres@localhost/soniq_demo
soniq setup main:soniq
uvicorn main:app --port 8000 &
soniq dashboard --app main:soniq --port 6161 &
BASE_URL=http://localhost:8000 ./demo.sh
```

The `soniq setup` command creates the database if it does not exist.

## Learn more

- [Transactional enqueue](../../docs/guides/transactional-enqueue.md)
- [FastAPI integration](../../docs/guides/fastapi.md)
