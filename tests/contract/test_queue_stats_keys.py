"""Closed-form key contract for ``Backend.get_queue_stats``.

Named directly from ``docs/_internals/contracts/queue_stats.md``: every backend must
return exactly the six canonical keys, no more and no fewer. An extra key
(e.g. a leaking column name) or a missing key fails the contract test
and blocks release gate 1.
"""

import os

import pytest

from soniq.testing.memory_backend import MemoryBackend

_CANONICAL_KEYS = {"total", "queued", "processing", "done", "dead_letter", "cancelled"}


def _backend_params() -> list[str]:
    params = ["memory"]
    try:
        import aiosqlite  # noqa: F401

        params.append("sqlite")
    except ImportError:
        pass
    if os.environ.get("SONIQ_DATABASE_URL"):
        params.append("postgres")
    return params


@pytest.fixture(params=_backend_params())
async def backend(request, tmp_path):
    if request.param == "memory":
        b = MemoryBackend()
        await b.initialize()
        try:
            yield b
        finally:
            await b.close()
        return

    if request.param == "sqlite":
        from soniq.backends.sqlite import SQLiteBackend

        b = SQLiteBackend(str(tmp_path / "queue_stats_keys.db"))
        await b.initialize()
        try:
            yield b
        finally:
            await b.close()
        return

    if request.param == "postgres":
        from soniq.backends.postgres import PostgresBackend
        from soniq.backends.postgres.migration_runner import run_migrations
        from tests.db_utils import TEST_DATABASE_URL

        b = PostgresBackend(database_url=TEST_DATABASE_URL)
        await b.initialize()

        async with b.acquire() as conn:
            await run_migrations(conn)

        async with b.acquire() as conn:
            await conn.execute("TRUNCATE soniq_jobs CASCADE")
            await conn.execute("TRUNCATE soniq_dead_letter_jobs CASCADE")
        try:
            yield b
        finally:
            async with b.acquire() as conn:
                await conn.execute("TRUNCATE soniq_jobs CASCADE")
                await conn.execute("TRUNCATE soniq_dead_letter_jobs CASCADE")
            await b.close()
        return

    raise AssertionError(f"unknown backend param: {request.param}")


@pytest.fixture(autouse=True)
async def clean_test_state():
    # Override the integration/conftest.py autouse fixture; this contract
    # test only touches a backend instance.
    yield


@pytest.mark.asyncio
async def test_get_queue_stats_returns_canonical_keys_only(backend):
    """Empty backend - the six canonical keys, all zero."""
    stats = await backend.get_queue_stats()
    assert set(stats.keys()) == _CANONICAL_KEYS, (
        f"backend {type(backend).__name__} returned non-canonical keys: "
        f"{set(stats.keys()) ^ _CANONICAL_KEYS}"
    )
    assert all(stats[k] == 0 for k in _CANONICAL_KEYS)


@pytest.mark.asyncio
async def test_get_queue_stats_keys_with_jobs_present(backend):
    """Populated backend - keys still match the canonical set, no leaks."""
    import uuid

    job_id = str(uuid.uuid4())
    await backend.create_job(
        job_id=job_id,
        job_name="t.keys",
        args={},
        args_hash=None,
        max_attempts=3,
        priority=100,
        queue="default",
        unique=False,
    )
    stats = await backend.get_queue_stats()
    assert set(stats.keys()) == _CANONICAL_KEYS
    assert stats["queued"] == 1
    assert stats["total"] == 1


@pytest.mark.asyncio
async def test_total_equals_sum_of_buckets(backend):
    """``total`` is computed from the five buckets, not a separate query."""
    import uuid

    for status_target in ("queued", "done", "dead_letter", "cancelled"):
        job_id = str(uuid.uuid4())
        await backend.create_job(
            job_id=job_id,
            job_name=f"t.{status_target}",
            args={},
            args_hash=None,
            max_attempts=3,
            priority=100,
            queue="default",
            unique=False,
        )
        if status_target == "done":
            await backend.mark_job_done(job_id)
        elif status_target == "dead_letter":
            await backend.mark_job_dead_letter(
                job_id, attempts=3, error="x", reason="max_retries_exceeded"
            )
        elif status_target == "cancelled":
            await backend.cancel_job(job_id)

    stats = await backend.get_queue_stats()
    assert (
        stats["total"]
        == stats["queued"]
        + stats["processing"]
        + stats["done"]
        + stats["dead_letter"]
        + stats["cancelled"]
    )
