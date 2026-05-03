"""
Dashboard DLQ data layer reads from the right table.

Per ``docs/_internals/contracts/dead_letter.md`` (Option A), dead-letter rows live
exclusively in ``soniq_dead_letter_jobs``. ``soniq_jobs.status`` does
not contain ``'dead_letter'`` (the column-level CHECK rejects it).

The dashboard's ``DashboardService.get_job_stats`` consults
``soniq_dead_letter_jobs`` for DLQ counts; ``replay_dead_letter``
delegates to ``DeadLetterService.replay`` so the DLQ row is preserved
as the audit trail and a fresh ``soniq_jobs`` row is enqueued.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest

from soniq import Soniq
from soniq.dashboard.app import DashboardService
from tests.db_utils import TEST_DATABASE_URL

pytestmark = pytest.mark.skipif(
    not os.environ.get("SONIQ_DATABASE_URL") and not TEST_DATABASE_URL,
    reason="Postgres test DB not configured",
)


@pytest.fixture
async def app():
    a = Soniq(database_url=TEST_DATABASE_URL)
    await a.ensure_initialized()
    pool = await a._get_pool()
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE soniq_jobs, soniq_dead_letter_jobs CASCADE")
    yield a
    await a.close()


async def _seed_dlq_row(
    app: Soniq, *, job_name: str = "test.dlq", queue: str = "default"
) -> str:
    job_id = str(uuid.uuid4())
    pool = await app._get_pool()
    now = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO soniq_dead_letter_jobs
                (id, job_name, args, queue, priority, max_attempts, attempts,
                 last_error, dead_letter_reason, original_created_at,
                 moved_to_dead_letter_at)
            VALUES ($1, $2, $3::jsonb, $4, 100, 3, 3, 'boom', 'max_retries', $5, $5)
            """,
            uuid.UUID(job_id),
            job_name,
            "{}",
            queue,
            now,
        )
    return job_id


@pytest.mark.asyncio
async def test_get_job_stats_dlq_count_reads_from_dlq_table(app):
    """get_job_stats()['dead_letter'] must reflect rows in soniq_dead_letter_jobs."""
    await _seed_dlq_row(app)
    await _seed_dlq_row(app)
    await _seed_dlq_row(app)

    data = DashboardService(app)
    stats = await data.get_job_stats()

    assert stats["dead_letter"] == 3, (
        f"Expected dead_letter=3 from DLQ table seeding, got "
        f"{stats['dead_letter']}. Dashboard is querying the wrong table."
    )


@pytest.mark.asyncio
async def test_replay_dead_letter_creates_new_queued_job_and_preserves_dlq_row(app):
    """``replay_dead_letter`` enqueues a fresh ``soniq_jobs`` row with a
    new id and increments ``resurrection_count`` on the DLQ row.

    The DLQ row is preserved as the audit trail; the original id stays
    in ``soniq_dead_letter_jobs`` and operators can replay multiple times.
    """

    # Register the seeded job_name so DeadLetterService.replay accepts it.
    @app.job(name="test.dlq")
    async def _noop() -> None:
        return None

    dlq_id = await _seed_dlq_row(app)
    data = DashboardService(app)

    new_job_id = await data.replay_dead_letter(dlq_id)
    assert new_job_id, "replay_dead_letter on a DLQ row must succeed"
    assert new_job_id != dlq_id, "Replay must mint a fresh soniq_jobs.id"

    pool = await app._get_pool()
    async with pool.acquire() as conn:
        new_row = await conn.fetchrow(
            "SELECT status FROM soniq_jobs WHERE id = $1", uuid.UUID(new_job_id)
        )
        dlq_row = await conn.fetchrow(
            "SELECT id, resurrection_count FROM soniq_dead_letter_jobs WHERE id = $1",
            uuid.UUID(dlq_id),
        )

    assert new_row is not None and new_row["status"] == "queued"
    assert dlq_row is not None, "DLQ row must be preserved as audit trail"
    assert dlq_row["resurrection_count"] == 1
