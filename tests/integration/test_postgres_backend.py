"""
Integration tests for PostgresBackend against a real PostgreSQL database.
"""

import uuid

import pytest

from soniq.backends.postgres import PostgresBackend
from tests.db_utils import TEST_DATABASE_URL


@pytest.fixture
async def backend():
    b = PostgresBackend(database_url=TEST_DATABASE_URL)
    await b.initialize()
    yield b
    await b.reset()
    await b.close()


@pytest.mark.asyncio
async def test_create_and_get_job(backend):
    job_id = str(uuid.uuid4())
    result = await backend.create_job(
        job_id=job_id,
        job_name="test.my_job",
        args={"x": 1},
        args_hash=None,
        max_attempts=3,
        priority=100,
        queue="default",
        unique=False,
        dedup_key=None,
        scheduled_at=None,
    )
    assert result == job_id

    job = await backend.get_job(job_id)
    assert job is not None
    assert job["id"] == job_id
    assert job["status"] == "queued"
    assert job["args"] == {"x": 1}


@pytest.mark.asyncio
async def test_fetch_and_lock_job(backend):
    job_id = str(uuid.uuid4())
    await backend.create_job(
        job_id=job_id,
        job_name="test.fetch_job",
        args={},
        args_hash=None,
        max_attempts=3,
        priority=100,
        queue="default",
        unique=False,
        dedup_key=None,
        scheduled_at=None,
    )

    locked = await backend.fetch_and_lock_job(queues=["default"], worker_id=None)
    assert locked is not None
    assert str(locked["id"]) == job_id

    # Job should now be processing
    job = await backend.get_job(job_id)
    assert job["status"] == "processing"

    # No more jobs to fetch
    empty = await backend.fetch_and_lock_job(queues=["default"], worker_id=None)
    assert empty is None


@pytest.mark.asyncio
async def test_mark_job_done(backend):
    job_id = str(uuid.uuid4())
    await backend.create_job(
        job_id=job_id,
        job_name="test.done_job",
        args={},
        args_hash=None,
        max_attempts=3,
        priority=100,
        queue="default",
        unique=False,
        dedup_key=None,
        scheduled_at=None,
    )
    await backend.fetch_and_lock_job(queues=["default"], worker_id=None)
    await backend.mark_job_done(job_id, result_ttl=3600)

    job = await backend.get_job(job_id)
    assert job["status"] == "done"


@pytest.mark.asyncio
async def test_mark_job_failed_and_retry(backend):
    job_id = str(uuid.uuid4())
    await backend.create_job(
        job_id=job_id,
        job_name="test.fail_job",
        args={},
        args_hash=None,
        max_attempts=3,
        priority=100,
        queue="default",
        unique=False,
        dedup_key=None,
        scheduled_at=None,
    )
    await backend.fetch_and_lock_job(queues=["default"], worker_id=None)
    await backend.mark_job_failed(job_id, attempts=1, error="boom")

    job = await backend.get_job(job_id)
    assert job["status"] == "queued"
    assert job["last_error"] == "boom"


@pytest.mark.asyncio
async def test_mark_job_dead_letter(backend):
    job_id = str(uuid.uuid4())
    await backend.create_job(
        job_id=job_id,
        job_name="test.dead_job",
        args={},
        args_hash=None,
        max_attempts=3,
        priority=100,
        queue="default",
        unique=False,
        dedup_key=None,
        scheduled_at=None,
    )
    await backend.fetch_and_lock_job(queues=["default"], worker_id=None)
    await backend.mark_job_dead_letter(
        job_id,
        attempts=3,
        error="gave up",
        reason="max_retries_exceeded",
    )

    # DLQ Option A: the row leaves soniq_jobs entirely.
    assert await backend.get_job(job_id) is None
    async with backend.acquire() as conn:
        dlq_row = await conn.fetchrow(
            "SELECT * FROM soniq_dead_letter_jobs WHERE id = $1",
            uuid.UUID(job_id),
        )
    assert dlq_row is not None
    assert dlq_row["dead_letter_reason"] == "max_retries_exceeded"
    assert dlq_row["last_error"] == "gave up"


@pytest.mark.asyncio
async def test_cancel_job(backend):
    job_id = str(uuid.uuid4())
    await backend.create_job(
        job_id=job_id,
        job_name="test.cancel_job",
        args={},
        args_hash=None,
        max_attempts=3,
        priority=100,
        queue="default",
        unique=False,
        dedup_key=None,
        scheduled_at=None,
    )

    result = await backend.cancel_job(job_id)
    assert result is True

    job = await backend.get_job(job_id)
    assert job["status"] == "cancelled"


@pytest.mark.asyncio
async def test_list_jobs_with_filters(backend):
    for i in range(3):
        await backend.create_job(
            job_id=str(uuid.uuid4()),
            job_name=f"test.list_job_{i}",
            args={},
            args_hash=None,
            max_attempts=3,
            priority=100,
            queue="q1" if i < 2 else "q2",
            unique=False,
            dedup_key=None,
            scheduled_at=None,
        )

    all_jobs = await backend.list_jobs()
    assert len(all_jobs) == 3

    q1_jobs = await backend.list_jobs(queue="q1")
    assert len(q1_jobs) == 2

    limited = await backend.list_jobs(limit=1)
    assert len(limited) == 1


@pytest.mark.asyncio
async def test_queue_stats(backend):
    await backend.create_job(
        job_id=str(uuid.uuid4()),
        job_name="test.stats_job",
        args={},
        args_hash=None,
        max_attempts=3,
        priority=100,
        queue="default",
        unique=False,
        dedup_key=None,
        scheduled_at=None,
    )

    stats = await backend.get_queue_stats()
    assert set(stats.keys()) == {
        "total",
        "queued",
        "processing",
        "done",
        "dead_letter",
        "cancelled",
    }
    assert stats["queued"] >= 1
    assert stats["total"] >= 1


@pytest.mark.asyncio
async def test_reset_clears_everything(backend):
    await backend.create_job(
        job_id=str(uuid.uuid4()),
        job_name="test.reset_job",
        args={},
        args_hash=None,
        max_attempts=3,
        priority=100,
        queue="default",
        unique=False,
        dedup_key=None,
        scheduled_at=None,
    )

    await backend.reset()

    jobs = await backend.list_jobs()
    assert len(jobs) == 0


@pytest.mark.asyncio
async def test_create_jobs_bulk_writes_all_rows(backend):
    """The Postgres bulk path lands every row with shared queue/priority/scheduled_at,
    JSONB-encoded args, and the supplied producer_id - in a single round trip."""
    from datetime import datetime, timedelta, timezone

    run_at = datetime.now(timezone.utc) + timedelta(minutes=10)
    job_ids = [str(uuid.uuid4()) for _ in range(5)]
    args_list = [{"index": i, "label": f"row-{i}"} for i in range(5)]

    await backend.create_jobs_bulk(
        job_ids=job_ids,
        job_name="test.bulk_insert",
        args_list=args_list,
        max_attempts=3,
        priority=42,
        queue="bulk",
        scheduled_at=run_at,
        producer_id="bulk-producer",
    )

    rows = await backend.list_jobs(queue="bulk")
    bulk_rows = [r for r in rows if r["job_name"] == "test.bulk_insert"]
    assert len(bulk_rows) == 5
    assert {r["id"] for r in bulk_rows} == set(job_ids)

    # JSONB roundtrip preserves dict structure.
    by_id = {r["id"]: r for r in bulk_rows}
    for jid, args in zip(job_ids, args_list):
        assert by_id[jid]["args"] == args
        assert by_id[jid]["queue"] == "bulk"
        assert by_id[jid]["priority"] == 42
        assert by_id[jid]["status"] == "queued"
        assert by_id[jid]["max_attempts"] == 3

    # Verify producer_id and scheduled_at via raw fetch (list_jobs strips some fields).
    async with backend.acquire() as conn:
        for jid in job_ids:
            row = await conn.fetchrow(
                "SELECT producer_id, scheduled_at FROM soniq_jobs WHERE id = $1",
                uuid.UUID(jid),
            )
            assert row["producer_id"] == "bulk-producer"
            assert abs((row["scheduled_at"] - run_at).total_seconds()) < 1


@pytest.mark.asyncio
async def test_create_jobs_bulk_empty_list_is_a_noop(backend):
    """An empty bulk call must not error and must not write any rows."""
    await backend.create_jobs_bulk(
        job_ids=[],
        job_name="test.bulk_empty",
        args_list=[],
        max_attempts=3,
        priority=100,
        queue="default",
    )
    rows = await backend.list_jobs()
    assert not any(r["job_name"] == "test.bulk_empty" for r in rows)
