"""get_job still finds a job after it moves to the dead-letter table."""

import uuid

import pytest

from soniq.testing.memory_backend import MemoryBackend


async def _dead_lettered_job(backend):
    job_id = str(uuid.uuid4())
    await backend.create_job(
        job_id=job_id,
        job_name="billing.charge",
        args={"order_id": 7},
        args_hash=None,
        max_attempts=2,
        priority=100,
        queue="default",
        unique=False,
        dedup_key=None,
        scheduled_at=None,
    )
    await backend.mark_job_dead_letter(
        job_id, attempts=2, error="boom", reason="max_retries_exceeded"
    )
    return job_id


async def _check(backend):
    job_id = await _dead_lettered_job(backend)
    job = await backend.get_job(job_id)
    assert job["status"] == "dead_letter"
    assert job["args"] == {"order_id": 7}
    assert job["last_error"] == "boom"
    assert job["dead_letter_reason"] == "max_retries_exceeded"
    assert await backend.get_job(str(uuid.uuid4())) is None


@pytest.mark.asyncio
async def test_sqlite(backend):
    await _check(backend)


@pytest.mark.asyncio
async def test_memory():
    await _check(MemoryBackend())
