"""Welcome email with FastAPI BackgroundTasks. The task lives only in process memory."""

import asyncio
import os

import asyncpg
from fastapi import BackgroundTasks, FastAPI

DATABASE_URL = os.environ["DATABASE_URL"]
SEND_SECONDS = float(os.environ.get("SEND_SECONDS", "5"))


async def send_welcome(user_id: int) -> None:
    await asyncio.sleep(SEND_SECONDS)  # stands in for a slow mail provider
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute(
            "INSERT INTO emails (user_id) VALUES ($1) ON CONFLICT DO NOTHING", user_id
        )
    finally:
        await conn.close()


app = FastAPI()


@app.post("/signup/{user_id}")
async def signup(user_id: int, background: BackgroundTasks):
    background.add_task(send_welcome, user_id)
    return {"ok": True}


@app.get("/health")
async def health():
    return {"ok": True}
