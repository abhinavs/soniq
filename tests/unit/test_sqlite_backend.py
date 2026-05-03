"""
Tests for SQLiteBackend — local dev backend with zero setup.
"""

import uuid

import pytest

pytest.importorskip("aiosqlite")


@pytest.mark.asyncio
async def test_sqlite_backend_importable():
    from soniq.backends.sqlite import SQLiteBackend

    assert SQLiteBackend is not None


@pytest.mark.asyncio
async def test_sqlite_satisfies_protocol():
    from soniq.backends import StorageBackend
    from soniq.backends.sqlite import SQLiteBackend

    backend = SQLiteBackend.__new__(SQLiteBackend)
    backend._path = ":memory:"
    backend._conn = None
    assert isinstance(backend, StorageBackend)


@pytest.mark.asyncio
async def test_sqlite_capabilities():
    from soniq.backends.sqlite import SQLiteBackend

    backend = SQLiteBackend(":memory:")
    assert backend.supports_push_notify is False
    assert backend.supports_transactional_enqueue is False


@pytest.mark.asyncio
async def test_sqlite_create_and_get(tmp_path):
    from soniq.backends.sqlite import SQLiteBackend

    backend = SQLiteBackend(str(tmp_path / "test.db"))
    await backend.initialize()

    try:
        job_id = str(uuid.uuid4())
        result = await backend.create_job(
            job_id=job_id,
            job_name="test.sqlite_job",
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
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_sqlite_full_lifecycle(tmp_path):
    from soniq.backends.sqlite import SQLiteBackend

    backend = SQLiteBackend(str(tmp_path / "lifecycle.db"))
    await backend.initialize()

    try:
        # Enqueue
        job_id = str(uuid.uuid4())
        await backend.create_job(
            job_id=job_id,
            job_name="test.lifecycle",
            args={},
            args_hash=None,
            max_attempts=3,
            priority=100,
            queue="default",
            unique=False,
            dedup_key=None,
            scheduled_at=None,
        )

        # Fetch and lock
        locked = await backend.fetch_and_lock_job(queues=["default"], worker_id=None)
        assert locked is not None
        assert (await backend.get_job(job_id))["status"] == "processing"

        # Mark done
        await backend.mark_job_done(job_id, result_ttl=300)
        assert (await backend.get_job(job_id))["status"] == "done"

        # Reset
        await backend.reset()
        assert await backend.list_jobs() == []
    finally:
        await backend.close()
