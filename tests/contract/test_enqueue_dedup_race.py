"""
Dedup enqueue under race conditions never returns a synthetic id.

Contract: every id returned from ``create_job`` resolves to a real row.
The Postgres path used to fall back to the caller-passed ``job_id`` when
the post-conflict lookup missed (e.g. the queued row transitioned to
processing between INSERT and SELECT). This test simulates that exact
race deterministically by hooking the connection between the two calls.
"""

from __future__ import annotations

import os
import uuid

import asyncpg
import pytest

from soniq.backends.postgres import PostgresBackend
from tests.db_utils import TEST_DATABASE_URL

pytestmark = pytest.mark.skipif(
    not os.environ.get("SONIQ_DATABASE_URL") and not TEST_DATABASE_URL,
    reason="Postgres test DB not configured",
)


@pytest.fixture
async def backend():
    b = PostgresBackend(database_url=TEST_DATABASE_URL)
    await b.initialize()
    yield b
    await b.reset()
    await b.close()


class _RaceConn:
    """Wraps an asyncpg.Connection. After the first ``fetchrow`` call,
    transitions any queued rows with the dedup_key out of 'queued' to
    simulate a worker picking them up between the failed INSERT and the
    fallback SELECT."""

    def __init__(self, real: asyncpg.Connection, dedup_key: str):
        self._real = real
        self._dedup_key = dedup_key
        self._fetchrow_calls = 0

    async def fetchrow(self, query: str, *args, **kwargs):
        result = await self._real.fetchrow(query, *args, **kwargs)
        self._fetchrow_calls += 1
        if self._fetchrow_calls == 1:
            await self._real.execute(
                "UPDATE soniq_jobs SET status = 'processing' "
                "WHERE dedup_key = $1 AND status = 'queued'",
                self._dedup_key,
            )
        return result

    def __getattr__(self, name):
        return getattr(self._real, name)


@pytest.mark.asyncio
async def test_dedup_returns_real_id_when_row_transitions_mid_flight(backend):
    """The exact race the synthetic-id fallback masks: queued row exists at
    INSERT time, transitions to processing before the post-INSERT lookup."""
    pool = backend._pool

    seed_id = await backend.create_job(
        job_id=str(uuid.uuid4()),
        job_name="test.race.midflight",
        args={},
        args_hash=None,
        max_attempts=3,
        priority=100,
        queue="default",
        unique=False,
        dedup_key="midflight:lock",
        scheduled_at=None,
    )

    caller_id = str(uuid.uuid4())
    async with pool.acquire() as raw_conn:
        race_conn = _RaceConn(raw_conn, "midflight:lock")
        returned_id = await backend._create_job_on_conn(
            race_conn,
            job_id=caller_id,
            job_name="test.race.midflight",
            args={},
            args_hash=None,
            max_attempts=3,
            priority=100,
            queue="default",
            unique=False,
            dedup_key="midflight:lock",
            scheduled_at=None,
        )

    assert returned_id != caller_id, (
        f"create_job returned the caller-provided id {caller_id!r}, which is "
        f"the synthetic-fallback bug: that id was never persisted."
    )

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, status FROM soniq_jobs WHERE id = $1",
            uuid.UUID(returned_id),
        )
    assert (
        row is not None
    ), f"Returned id {returned_id!r} does not resolve to any soniq_jobs row."
    assert (
        str(row["id"]) == seed_id
    ), f"Returned id should be the seed row's id ({seed_id}), got {returned_id}"
