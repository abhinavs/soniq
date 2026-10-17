"""FastAPI + SQLAlchemy + Soniq demo.

Each route writes a row and enqueues a job in one transaction.
If the transaction rolls back, the job is never created.
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import Boolean, String, func, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from soniq import Soniq
from soniq.integrations.fastapi import lifespan

DATABASE_URL = os.environ.get(
    "SONIQ_DATABASE_URL", "postgresql://postgres@localhost/soniq_demo"
)
DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "http://localhost:6161")

soniq = Soniq(database_url=DATABASE_URL)
engine = create_async_engine(
    DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
)
Session = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "demo_users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(200))
    welcome_sent: Mapped[bool] = mapped_column(Boolean, default=False)


class Order(Base):
    __tablename__ = "demo_orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    item: Mapped[str] = mapped_column(String(200))


@soniq.job(name="send_welcome_email", queue="emails", retries=3)
async def send_welcome_email(user_id: int):
    # Handlers can run twice, so this only sets a flag.
    async with Session() as db, db.begin():
        await db.execute(
            update(User).where(User.id == user_id).values(welcome_sent=True)
        )
    print(f"welcome email sent to user {user_id}", flush=True)


@soniq.job(name="process_order", queue="orders", retries=3)
async def process_order(order_id: int):
    print(f"processing order {order_id}", flush=True)


@asynccontextmanager
async def create_tables(app):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


app = FastAPI(
    title="Soniq demo",
    lifespan=lifespan(soniq, run_worker=True, concurrency=2, extra=create_tables),
)


@app.get("/", response_class=HTMLResponse)
async def index():
    return (
        "<h1>Soniq demo</h1>"
        f'<p><a href="{DASHBOARD_URL}">Open the dashboard</a></p>'
        '<p><a href="/docs">API docs</a></p>'
    )


@app.post("/signup")
async def signup(email: str):
    async with Session() as db, db.begin():
        user = User(email=email)
        db.add(user)
        await db.flush()  # gives us user.id without committing
        job_id = await soniq.enqueue(send_welcome_email, connection=db, user_id=user.id)
    return {"user_id": user.id, "job_id": job_id}


@app.post("/orders")
async def create_order(item: str, fail: int = 0):
    try:
        async with Session() as db, db.begin():
            order = Order(item=item)
            db.add(order)
            await db.flush()
            await soniq.enqueue(process_order, connection=db, order_id=order.id)
            if fail:
                raise RuntimeError("payment failed")  # rolls back order and job
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"order_id": order.id}


@app.get("/state")
async def state():
    """Row counts, so you can see what was committed."""
    async with Session() as db:
        users = await db.scalar(select(func.count()).select_from(User))
        sent = await db.scalar(
            select(func.count()).select_from(User).where(User.welcome_sent)
        )
        orders = await db.scalar(select(func.count()).select_from(Order))
        rows = await db.execute(
            text("SELECT job_name, status, count(*) FROM soniq_jobs GROUP BY 1, 2")
        )
    return {
        "users": users,
        "welcome_sent": sent,
        "orders": orders,
        "jobs": {f"{n}:{s}": c for n, s, c in rows},
    }
