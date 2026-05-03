"""
Transactional enqueue example for Soniq.

Demonstrates how to enqueue a job inside an existing database transaction.
If the transaction rolls back, the job is never created - your data and
your job are committed atomically.

Usage:
    pip install soniq fastapi uvicorn
    export SONIQ_DATABASE_URL="postgresql://postgres@localhost/soniq"
    soniq setup
    uvicorn examples.transactional_enqueue:app --reload
"""

import asyncpg
from fastapi import FastAPI
from pydantic import BaseModel

from soniq import Soniq

DATABASE_URL = "postgresql://postgres@localhost/soniq"

soniq_app = Soniq(database_url=DATABASE_URL)


@soniq_app.job(name="send_welcome_email", queue="emails", retries=3)
async def send_welcome_email(user_id: int, email: str):
    """Send a welcome email to a newly registered user."""
    print(f"Sending welcome email to {email} (user {user_id})")


class CreateUserRequest(BaseModel):
    name: str
    email: str


from contextlib import asynccontextmanager  # noqa: E402


@asynccontextmanager
async def lifespan(app):
    app.state.pool = await asyncpg.create_pool(DATABASE_URL)
    await soniq_app.setup()

    async with app.state.pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE
            )
        """
        )

    yield

    await app.state.pool.close()
    await soniq_app.close()


app = FastAPI(title="Soniq Transactional Enqueue Demo", lifespan=lifespan)


@app.post("/users")
async def create_user(req: CreateUserRequest):
    """
    Create a user and enqueue a welcome email - atomically.

    If the INSERT fails (e.g. duplicate email), the job is never enqueued.
    If the enqueue fails, the user row is rolled back.
    """
    async with app.state.pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "INSERT INTO users (name, email) VALUES ($1, $2) RETURNING id",
                req.name,
                req.email,
            )
            user_id = row["id"]

            job_id = await soniq_app.enqueue(
                "send_welcome_email",
                args={"user_id": user_id, "email": req.email},
                connection=conn,
            )

    return {"user_id": user_id, "welcome_email_job": job_id}
