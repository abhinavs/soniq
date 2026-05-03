"""
Integration test for Snooze against the Postgres backend.

Confirms end-to-end that a job returning Snooze ends up queued with its
attempts counter rolled back and scheduled_at advanced, and that a second
pass executes the job normally.
"""

import asyncio
from datetime import datetime, timezone

import pytest

from soniq import Soniq
from soniq.job import Snooze
from tests.db_utils import TEST_DATABASE_URL


@pytest.mark.asyncio
async def test_snooze_requeues_with_attempts_unchanged_against_postgres():
    app = Soniq(database_url=TEST_DATABASE_URL)
    await app._ensure_initialized()

    calls = {"n": 0}

    @app.job(retries=1, name="snooze_then_succeed")
    async def snooze_then_succeed():
        calls["n"] += 1
        if calls["n"] == 1:
            return Snooze(seconds=0.5, reason="first pass")
        return "ok"

    job_id = await app.enqueue("snooze_then_succeed")

    processed = await app._backend.fetch_and_lock_job(queues=None, worker_id=None)
    assert processed is not None

    from soniq.core.processor import process_job_via_backend

    async with app.backend._pool.acquire() as conn:
        await conn.execute(
            "UPDATE soniq_jobs SET status='queued', attempts=0 WHERE id=$1",
            job_id,
        )

    result = await process_job_via_backend(
        backend=app._backend, job_registry=app._job_registry
    )
    assert result is True

    async with app.backend._pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, attempts, scheduled_at, last_error FROM soniq_jobs WHERE id=$1",
            job_id,
        )
    assert row["status"] == "queued"
    assert row["attempts"] == 0, "snooze must not consume an attempt slot"
    assert row["scheduled_at"] is not None
    assert row["last_error"].startswith("SNOOZE")
    delta = (row["scheduled_at"] - datetime.now(timezone.utc)).total_seconds()
    assert -1 <= delta <= 2

    await asyncio.sleep(0.6)
    result = await process_job_via_backend(
        backend=app._backend, job_registry=app._job_registry
    )
    assert result is True

    async with app.backend._pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, attempts FROM soniq_jobs WHERE id=$1", job_id
        )
    assert row["status"] == "done"
    assert row["attempts"] == 1
    assert calls["n"] == 2

    await app.close()
