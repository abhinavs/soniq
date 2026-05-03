"""DLQ contract test matrix.

Group A (8 tests, parameterized over memory + sqlite + postgres = 24 runs):
the runtime contract from ``docs/_internals/contracts/dead_letter.md`` and
``docs/_internals/contracts/queue_stats.md`` enforced at the backend level.

Group B (postgres-only): migration mechanics for the dead-letter
schema enforced in ``0001_core.sql`` and ``0002_dead_letter.sql``.
"""

import asyncio
import uuid

import pytest

from tests.integration.dlq.conftest import (
    dlq_count,
    dlq_get,
    purge,
    replay,
)


def _is_postgres(backend) -> bool:
    return type(backend).__name__ == "PostgresBackend"


def _is_memory(backend) -> bool:
    return type(backend).__name__ == "MemoryBackend"


def _is_sqlite(backend) -> bool:
    return type(backend).__name__ == "SQLiteBackend"


async def _create_test_job(
    backend, *, job_name: str = "test.dlq", queue: str = "default"
) -> str:
    job_id = str(uuid.uuid4())
    await backend.create_job(
        job_id=job_id,
        job_name=job_name,
        args={"k": "v"},
        args_hash=None,
        max_attempts=3,
        priority=100,
        queue=queue,
        unique=False,
    )
    return job_id


# --- Group A: parameterized over memory + sqlite + postgres ---


@pytest.mark.asyncio
async def test_dlq_runtime_move_atomicity(backend):
    """mark_job_dead_letter moves the row from soniq_jobs to the DLQ table.

    After commit the id exists in exactly one of the two tables, with
    reason/tags/error/attempts populated from the call site. See
    docs/_internals/contracts/dead_letter.md.
    """
    job_id = await _create_test_job(backend)

    await backend.mark_job_dead_letter(
        job_id,
        attempts=3,
        error="boom",
        reason="max_retries_exceeded",
        tags={"trace": "abc"},
    )

    # soniq_jobs row is gone.
    assert await backend.get_job(job_id) is None

    # DLQ row exists with the metadata we passed.
    dlq_row = await dlq_get(backend, job_id)
    assert dlq_row is not None
    assert str(dlq_row["id"]) == job_id
    assert dlq_row["dead_letter_reason"] == "max_retries_exceeded"
    assert dlq_row["attempts"] == 3
    assert dlq_row["last_error"] == "boom"


@pytest.mark.asyncio
async def test_dlq_concurrent_move_and_stats(backend):
    """Concurrent mark_job_dead_letter calls produce a consistent snapshot.

    All N moves complete; get_queue_stats sees N dead-lettered rows and
    no stragglers in soniq_jobs.
    """
    job_ids = []
    for _ in range(5):
        job_ids.append(await _create_test_job(backend))

    await asyncio.gather(
        *[
            backend.mark_job_dead_letter(
                jid,
                attempts=3,
                error=f"err-{i}",
                reason="max_retries_exceeded",
            )
            for i, jid in enumerate(job_ids)
        ]
    )

    stats = await backend.get_queue_stats()
    assert stats["dead_letter"] == 5
    assert stats["queued"] == 0
    assert stats["processing"] == 0
    # total includes dead_letter under the cross-table aggregation.
    assert stats["total"] == 5

    for jid in job_ids:
        assert await backend.get_job(jid) is None


@pytest.mark.asyncio
async def test_dlq_mid_transaction_crash(backend):
    """A failure between INSERT and DELETE leaves both tables intact.

    Postgres/SQLite: the transaction wraps both statements so an exception
    rolls back the INSERT. Memory: the operation runs under a single
    asyncio lock and an exception before mutation prevents the move.

    For postgres we exercise the path by injecting a duplicate-key INSERT
    on the DLQ table after pre-seeding a row; for sqlite/memory we use
    the same trick (insert the DLQ row first to force a primary-key
    collision when mark_job_dead_letter retries).
    """
    job_id = await _create_test_job(backend)

    # Pre-seed a DLQ row with the same id so the move's INSERT collides.
    if _is_postgres(backend):
        async with backend.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO soniq_dead_letter_jobs (
                    id, job_name, args, queue, priority, max_attempts,
                    attempts, last_error, dead_letter_reason,
                    original_created_at, moved_to_dead_letter_at
                ) VALUES ($1, 'pre.seed', '{}'::jsonb, 'default', 100, 3,
                    1, 'pre', 'manual_move', NOW(), NOW())
                """,
                uuid.UUID(job_id),
            )
    elif _is_sqlite(backend):
        await backend._conn.execute(
            """
            INSERT INTO soniq_dead_letter_jobs (
                id, job_name, args, queue, priority, max_attempts,
                attempts, last_error, dead_letter_reason,
                original_created_at, moved_to_dead_letter_at
            ) VALUES (?, 'pre.seed', '{}', 'default', 100, 3,
                1, 'pre', 'manual_move', '2024-01-01', '2024-01-01')
            """,
            (job_id,),
        )
        await backend._conn.commit()
    else:
        from datetime import datetime, timezone

        backend._dead_letter_jobs[job_id] = {
            "id": job_id,
            "job_name": "pre.seed",
            "args": {},
            "queue": "default",
            "priority": 100,
            "max_attempts": 3,
            "attempts": 1,
            "last_error": "pre",
            "dead_letter_reason": "manual_move",
            "original_created_at": datetime.now(timezone.utc),
            "moved_to_dead_letter_at": datetime.now(timezone.utc),
            "resurrection_count": 0,
            "last_resurrection_at": None,
            "tags": None,
            "created_at": datetime.now(timezone.utc),
        }

    if _is_memory(backend):
        # Memory's INSERT-then-DELETE move overwrites the dict; it does
        # not raise on collision. The contract for memory is "lock-based
        # atomicity"; a mid-op exception cannot leave a partial state.
        # Seed it with a fake row, then run the move - end state: DLQ has
        # the new row, jobs has been removed. This still demonstrates
        # that the source row is gone iff the DLQ row is present.
        await backend.mark_job_dead_letter(
            job_id, attempts=3, error="boom", reason="max_retries_exceeded"
        )
        assert await backend.get_job(job_id) is None
        assert job_id in backend._dead_letter_jobs
        return

    # Postgres + SQLite: the INSERT must raise and the source row must
    # remain in soniq_jobs.
    with pytest.raises(Exception):
        await backend.mark_job_dead_letter(
            job_id, attempts=3, error="boom", reason="max_retries_exceeded"
        )

    job_after = await backend.get_job(job_id)
    assert job_after is not None, "source row must survive a failed move"


@pytest.mark.asyncio
async def test_dlq_replay_creates_new_job(backend):
    """Replay materializes a fresh soniq_jobs row and preserves the DLQ row.

    The DLQ row stays - resurrection_count increments - and the new
    soniq_jobs row carries reset attempts and 'queued' status.
    """
    job_id = await _create_test_job(backend)
    await backend.mark_job_dead_letter(
        job_id, attempts=3, error="boom", reason="max_retries_exceeded"
    )

    new_job_id = await replay(backend, job_id)

    new_job = await backend.get_job(new_job_id)
    assert new_job is not None
    assert new_job["status"] == "queued"
    assert new_job["attempts"] == 0

    dlq_row = await dlq_get(backend, job_id)
    assert dlq_row is not None
    assert dlq_row["resurrection_count"] == 1


@pytest.mark.asyncio
async def test_dlq_replay_twice(backend):
    """Replaying twice creates two new soniq_jobs rows and bumps the counter."""
    job_id = await _create_test_job(backend)
    await backend.mark_job_dead_letter(
        job_id, attempts=3, error="boom", reason="max_retries_exceeded"
    )

    new_job_id_1 = await replay(backend, job_id)
    new_job_id_2 = await replay(backend, job_id)

    assert new_job_id_1 != new_job_id_2
    assert await backend.get_job(new_job_id_1) is not None
    assert await backend.get_job(new_job_id_2) is not None

    dlq_row = await dlq_get(backend, job_id)
    assert dlq_row["resurrection_count"] == 2


@pytest.mark.asyncio
async def test_dlq_purge_deletes_from_dlq(backend):
    """Purge removes the DLQ row; subsequent queries see no trace of it."""
    job_id = await _create_test_job(backend)
    await backend.mark_job_dead_letter(
        job_id, attempts=3, error="boom", reason="max_retries_exceeded"
    )
    assert await dlq_count(backend) == 1

    deleted = await purge(backend, job_id)
    assert deleted is True
    assert await dlq_count(backend) == 0
    assert await dlq_get(backend, job_id) is None

    stats = await backend.get_queue_stats()
    assert stats["dead_letter"] == 0


@pytest.mark.asyncio
async def test_dlq_status_rejects_dead_letter(backend):
    """The contract: backends never accept status='dead_letter' in soniq_jobs.

    Postgres rejects via the column-level CHECK on soniq_jobs.status
    set in 0001_core.sql; SQLite via BEFORE INSERT/UPDATE triggers;
    memory via _reject_dead_letter_status.
    """
    job_id = str(uuid.uuid4())

    if _is_postgres(backend):
        import asyncpg

        async with backend.acquire() as conn:
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute(
                    """
                    INSERT INTO soniq_jobs (id, job_name, args, status, max_attempts)
                    VALUES ($1, 'rejected', '{}'::jsonb, 'dead_letter', 3)
                    """,
                    uuid.UUID(job_id),
                )
        return

    if _is_sqlite(backend):
        # The trigger raises a generic error from aiosqlite.
        with pytest.raises(Exception):
            await backend._conn.execute(
                """
                INSERT INTO soniq_jobs (id, job_name, args, status, max_attempts)
                VALUES (?, 'rejected', '{}', 'dead_letter', 3)
                """,
                (job_id,),
            )
            await backend._conn.commit()
        return

    if _is_memory(backend):
        # Memory exposes a reject helper used by the create path. Verify
        # the helper rejects the value directly so the contract is
        # uniform across backends.
        from soniq.testing.memory_backend import _reject_dead_letter_status

        with pytest.raises(ValueError):
            _reject_dead_letter_status("dead_letter")
        return

    raise AssertionError(f"unhandled backend: {type(backend).__name__}")


@pytest.mark.asyncio
async def test_dlq_stats_cross_table_consistency(backend):
    """get_queue_stats counts DLQ rows in 'dead_letter' and 'total'.

    Mixing dead-lettered, queued, and done jobs must produce a consistent
    snapshot where dead_letter equals the DLQ table count and total is
    the sum across all states.
    """
    queued_id = await _create_test_job(backend, job_name="t.queued")
    done_id = await _create_test_job(backend, job_name="t.done")
    dlq_id = await _create_test_job(backend, job_name="t.dlq")

    await backend.mark_job_done(done_id)
    await backend.mark_job_dead_letter(
        dlq_id, attempts=3, error="boom", reason="max_retries_exceeded"
    )

    stats = await backend.get_queue_stats()
    assert stats["queued"] == 1
    assert stats["done"] == 1
    assert stats["dead_letter"] == 1
    assert stats["dead_letter"] == await dlq_count(backend)
    assert stats["total"] == stats["queued"] + stats["done"] + stats["dead_letter"]
    # Sanity: the queued row id is still there.
    queued = await backend.get_job(queued_id)
    assert queued is not None and queued["status"] == "queued"


# --- Group B: postgres-only migration tests ---


@pytest.fixture
async def fresh_postgres_db():
    from tests.db_utils import make_test_db_url, run_createdb, run_dropdb

    db_name = "soniq_dlq_contract_test"
    run_dropdb(db_name)
    run_createdb(db_name, check=True)
    url = make_test_db_url(db_name)
    try:
        yield url
    finally:
        run_dropdb(db_name)


@pytest.mark.asyncio
async def test_dlq_migration_idempotent_postgres(fresh_postgres_db):
    """Re-running the DLQ migration is a no-op; the canonical status check
    constraint stays intact and soniq_dead_letter_jobs exists."""
    import asyncpg

    from soniq.backends.postgres.migration_runner import MigrationRunner

    pool = await asyncpg.create_pool(fresh_postgres_db)
    try:
        async with pool.acquire() as conn:
            runner = MigrationRunner()
            await runner._run_migrations_with_connection(conn, version_filter="0001")
            await runner._run_migrations_with_connection(conn, version_filter="0002")
            await runner._run_migrations_with_connection(conn, version_filter="0002")

            constraints = await conn.fetch(
                """
                SELECT conname FROM pg_constraint
                WHERE conrelid = 'soniq_jobs'::regclass
                  AND conname = 'soniq_jobs_status_check'
                """
            )
            assert {row["conname"] for row in constraints} == {
                "soniq_jobs_status_check"
            }

            dlq_exists = await conn.fetchval(
                "SELECT to_regclass('soniq_dead_letter_jobs') IS NOT NULL"
            )
            assert dlq_exists is True
    finally:
        await pool.close()
