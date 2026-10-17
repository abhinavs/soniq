"""Same app, but the welcome email is a Soniq job stored in Postgres."""

import asyncio
import os

import asyncpg
from fastapi import FastAPI

from soniq import Soniq
from soniq.integrations.fastapi import lifespan

DATABASE_URL = os.environ["DATABASE_URL"]
SEND_SECONDS = float(os.environ.get("SEND_SECONDS", "5"))

soniq = Soniq(database_url=DATABASE_URL)


@soniq.job()
async def send_welcome(user_id: int) -> None:
    await asyncio.sleep(SEND_SECONDS)  # stands in for a slow mail provider
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute(
            "INSERT INTO emails (user_id) VALUES ($1) ON CONFLICT DO NOTHING", user_id
        )
    finally:
        await conn.close()


app = FastAPI(lifespan=lifespan(soniq, run_worker=True, concurrency=2))


@app.post("/signup/{user_id}")
async def signup(user_id: int):
    await soniq.enqueue(send_welcome, user_id=user_id)
    return {"ok": True}


@app.get("/health")
async def health():
    return {"ok": True}
