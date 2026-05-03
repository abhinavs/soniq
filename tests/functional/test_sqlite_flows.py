"""
Core job lifecycle tests using SQLiteBackend.

Proves the same flows that work with MemoryBackend also work with SQLite.
No PostgreSQL required.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("aiosqlite")

from soniq.core.processor import process_job_via_backend  # noqa: E402
from soniq.core.registry import JobRegistry  # noqa: E402


@pytest.fixture
def registry():
    return JobRegistry()


async def _create(backend, registry, func, args=None, **kw):
    registry.register_job(
        func, **{k: v for k, v in kw.items() if k in ("max_retries",)}
    )
    job_name = f"{func.__module__}.{func.__name__}"
    job_id = str(uuid.uuid4())
    job_meta = registry.get_job(job_name)
    max_attempts = (
        kw.get("max_retries", job_meta["max_retries"]) if job_meta else 3
    ) + 1

    await backend.create_job(
        job_id=job_id,
        job_name=job_name,
        args=args or {},
        args_hash=None,
        max_attempts=max_attempts,
        priority=kw.get("priority", 100),
        queue="default",
        unique=False,
        dedup_key=None,
        scheduled_at=kw.get("scheduled_at"),
    )
    return job_id


@pytest.mark.asyncio
async def test_enqueue_and_process(backend, registry):
    executed = []

    async def my_task(msg: str):
        executed.append(msg)

    job_id = await _create(backend, registry, my_task, {"msg": "hi"})
    await process_job_via_backend(
        backend=backend, job_registry=registry, queues=["default"]
    )

    assert executed == ["hi"]
    job = await backend.get_job(job_id)
    assert job["status"] == "done"


@pytest.mark.asyncio
async def test_retry_on_failure(backend, registry):
    async def bad_task():
        raise RuntimeError("fail")

    job_id = await _create(backend, registry, bad_task, max_retries=2)
    await process_job_via_backend(
        backend=backend, job_registry=registry, queues=["default"]
    )

    job = await backend.get_job(job_id)
    assert job["status"] == "queued"
    assert job["attempts"] == 1


@pytest.mark.asyncio
async def test_dead_letter_after_max(backend, registry):
    async def bad_task():
        raise RuntimeError("fail")

    job_id = await _create(backend, registry, bad_task, max_retries=0)
    await process_job_via_backend(
        backend=backend, job_registry=registry, queues=["default"]
    )

    # DLQ Option A: dead-lettered rows leave soniq_jobs and live in
    # soniq_dead_letter_jobs as the single source of truth.
    assert await backend.get_job(job_id) is None
    async with backend._conn.execute(
        "SELECT COUNT(*) AS c FROM soniq_dead_letter_jobs WHERE id=?", (job_id,)
    ) as cursor:
        row = await cursor.fetchone()
    assert int(row["c"]) == 1


@pytest.mark.asyncio
async def test_scheduled_not_early(backend, registry):
    async def my_task():
        pass

    future = datetime.now(timezone.utc) + timedelta(hours=1)
    await _create(backend, registry, my_task, scheduled_at=future)

    result = await process_job_via_backend(
        backend=backend, job_registry=registry, queues=["default"]
    )
    assert result is False


@pytest.mark.asyncio
async def test_cancel_and_list(backend, registry):
    async def my_task():
        pass

    job_id = await _create(backend, registry, my_task)

    assert await backend.cancel_job(job_id) is True
    jobs = await backend.list_jobs(status="cancelled")
    assert len(jobs) == 1


@pytest.mark.asyncio
async def test_reset(backend, registry):
    async def my_task():
        pass

    await _create(backend, registry, my_task)
    assert len(await backend.list_jobs()) == 1

    await backend.reset()
    assert len(await backend.list_jobs()) == 0


@pytest.mark.asyncio
async def test_result_persisted_and_retrieved_sqlite(backend, registry):
    """A job's return value is stored and readable via get_job on SQLite."""

    async def compute():
        return {"answer": 42, "items": [1, 2, 3]}

    job_id = await _create(backend, registry, compute)
    await process_job_via_backend(
        backend=backend, job_registry=registry, queues=["default"]
    )

    job = await backend.get_job(job_id)
    assert job["status"] == "done"
    assert job["result"] == {"answer": 42, "items": [1, 2, 3]}


@pytest.mark.asyncio
async def test_result_none_when_void_sqlite(backend, registry):
    """A void handler stores NULL, not the string 'null'."""

    async def no_return():
        return None

    job_id = await _create(backend, registry, no_return)
    await process_job_via_backend(
        backend=backend, job_registry=registry, queues=["default"]
    )

    job = await backend.get_job(job_id)
    assert job["status"] == "done"
    assert job["result"] is None
