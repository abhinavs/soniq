"""
Tests that job return values are persisted and retrievable through the Postgres
backend.

The README advertises "Job results -- store and retrieve return values from
completed jobs" as a feature. Before this PR, `mark_job_done` accepted a
`result` parameter but silently dropped it: the `soniq_jobs` table had no
`result` column and `get_job` did not reference one. These tests pin the
fix.
"""

import uuid

import pytest

from soniq import Soniq
from soniq.backends.postgres import PostgresBackend
from soniq.core.processor import process_job_via_backend
from tests.db_utils import TEST_DATABASE_URL


@pytest.fixture
async def backend():
    b = PostgresBackend(database_url=TEST_DATABASE_URL)
    await b.initialize()
    yield b
    await b.reset()
    await b.close()


@pytest.mark.asyncio
async def test_result_persisted_and_retrieved_postgres(backend):
    """A job's return value is written to the jobs table and read back via get_job."""
    job_id = str(uuid.uuid4())
    await backend.create_job(
        job_id=job_id,
        job_name="test.result_job",
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
    await backend.mark_job_done(
        job_id, result_ttl=3600, result={"count": 42, "name": "abc"}
    )

    job = await backend.get_job(job_id)
    assert job is not None
    assert job["status"] == "done"
    assert job["result"] == {"count": 42, "name": "abc"}


@pytest.mark.asyncio
async def test_result_none_when_handler_returns_nothing(backend):
    """A job that returns None stores NULL (not the string 'null')."""
    job_id = str(uuid.uuid4())
    await backend.create_job(
        job_id=job_id,
        job_name="test.void_job",
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
    await backend.mark_job_done(job_id, result_ttl=3600, result=None)

    job = await backend.get_job(job_id)
    assert job["status"] == "done"
    assert job["result"] is None


@pytest.mark.asyncio
async def test_end_to_end_get_result_via_app():
    """Enqueue via instance API, process, retrieve result."""
    app = Soniq(database_url=TEST_DATABASE_URL)

    @app.job(name="compute_total")
    async def compute_total(a: int, b: int):
        return {"total": a + b}

    await app.setup()
    job_id = await app.enqueue("compute_total", args={"a": 2, "b": 40})

    processed = await process_job_via_backend(
        backend=app._backend,
        job_registry=app._get_job_registry(),
        queues=["default"],
    )
    assert processed is True

    result = await app.get_result(job_id)
    assert result == {"total": 42}

    await app.close()


@pytest.mark.asyncio
async def test_result_survives_json_roundtrip(backend):
    """Complex JSON-serializable types (list, nested dict) round-trip intact."""
    job_id = str(uuid.uuid4())
    payload = {
        "items": [1, 2, 3],
        "meta": {"ok": True, "ratio": 0.5},
        "tags": ["a", "b"],
    }
    await backend.create_job(
        job_id=job_id,
        job_name="test.complex_result",
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
    await backend.mark_job_done(job_id, result_ttl=3600, result=payload)

    job = await backend.get_job(job_id)
    assert job["result"] == payload
