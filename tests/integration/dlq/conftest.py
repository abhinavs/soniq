"""Backend fixture for DLQ contract tests parameterized over memory, sqlite, and postgres.

Overrides the ``clean_test_state`` autouse fixture from
``tests/integration/conftest.py`` because these tests work directly with
backend instances and do not use ``soniq._global_app``.
"""

import os
import uuid

import pytest

from soniq.testing.memory_backend import MemoryBackend


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


@pytest.fixture(autouse=True)
async def clean_test_state():
    # Override the integration/conftest.py fixture: DLQ contract tests do
    # not touch the global app and starting a global app per test would
    # add an unrelated postgres dependency to memory/sqlite runs.
    yield


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

        b = SQLiteBackend(str(tmp_path / "dlq_contract.db"))
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

        # All soniq tables (including soniq_dead_letter_jobs) ship in the
        # core migration set. Apply migrations against the shared test DB
        # so the table exists regardless of test ordering.
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


def new_job_id() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def make_job_id():
    return new_job_id


# ---------------------------------------------------------------------------
# DLQ helpers - thin per-backend adapters used by replay/purge tests so the
# parameterized matrix can exercise the same contract on backends that have no
# DeadLetterService implementation. Postgres goes through DeadLetterService;
# memory/sqlite manipulate backend state directly because the service is
# postgres-only.
# ---------------------------------------------------------------------------


async def _pg_replay(backend, dead_letter_id: str) -> str:
    new_job_id_val = str(uuid.uuid4())
    async with backend.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT * FROM soniq_dead_letter_jobs WHERE id = $1 FOR UPDATE",
                uuid.UUID(dead_letter_id),
            )
            if row is None:
                raise AssertionError(f"DLQ row {dead_letter_id} not found")
            await conn.execute(
                """
                INSERT INTO soniq_jobs (
                    id, job_name, args, max_attempts, priority, queue,
                    attempts, status, scheduled_at
                ) VALUES ($1, $2, $3, $4, $5, $6, 0, 'queued', NOW())
                """,
                uuid.UUID(new_job_id_val),
                row["job_name"],
                row["args"],
                row["max_attempts"],
                row["priority"],
                row["queue"],
            )
            await conn.execute(
                """
                UPDATE soniq_dead_letter_jobs
                SET resurrection_count = resurrection_count + 1,
                    last_resurrection_at = NOW()
                WHERE id = $1
                """,
                uuid.UUID(dead_letter_id),
            )
    return new_job_id_val


async def _sqlite_replay(backend, dead_letter_id: str) -> str:
    import json as _json
    from datetime import datetime, timezone

    new_job_id_val = str(uuid.uuid4())
    assert backend._conn is not None
    async with backend._conn.execute(
        "SELECT * FROM soniq_dead_letter_jobs WHERE id=?", (dead_letter_id,)
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        raise AssertionError(f"DLQ row {dead_letter_id} not found")
    args_payload = row["args"]
    # SQLite stores args as TEXT; reuse as-is.
    if not isinstance(args_payload, str):
        args_payload = _json.dumps(args_payload)
    await backend.create_job(
        job_id=new_job_id_val,
        job_name=row["job_name"],
        args=_json.loads(args_payload),
        args_hash=None,
        max_attempts=row["max_attempts"],
        priority=row["priority"],
        queue=row["queue"],
        unique=False,
    )
    await backend._conn.execute(
        "UPDATE soniq_dead_letter_jobs "
        "SET resurrection_count = resurrection_count + 1, "
        "    last_resurrection_at = ? "
        "WHERE id=?",
        (
            datetime.now(timezone.utc).isoformat(),
            dead_letter_id,
        ),
    )
    await backend._conn.commit()
    return new_job_id_val


async def _memory_replay(backend, dead_letter_id: str) -> str:
    from datetime import datetime, timezone

    new_job_id_val = str(uuid.uuid4())
    dlq = backend._dead_letter_jobs.get(dead_letter_id)
    if dlq is None:
        raise AssertionError(f"DLQ row {dead_letter_id} not found")
    await backend.create_job(
        job_id=new_job_id_val,
        job_name=dlq["job_name"],
        args=dict(dlq["args"]),
        args_hash=None,
        max_attempts=dlq["max_attempts"],
        priority=dlq["priority"],
        queue=dlq["queue"],
        unique=False,
    )
    dlq["resurrection_count"] = dlq.get("resurrection_count", 0) + 1
    dlq["last_resurrection_at"] = datetime.now(timezone.utc)
    return new_job_id_val


async def replay(backend, dead_letter_id: str) -> str:
    """Backend-agnostic replay: copy DLQ row to soniq_jobs, bump counter."""
    cls_name = type(backend).__name__
    if cls_name == "PostgresBackend":
        return await _pg_replay(backend, dead_letter_id)
    if cls_name == "SQLiteBackend":
        return await _sqlite_replay(backend, dead_letter_id)
    if cls_name == "MemoryBackend":
        return await _memory_replay(backend, dead_letter_id)
    raise AssertionError(f"unknown backend: {cls_name}")


async def purge(backend, dead_letter_id: str) -> bool:
    cls_name = type(backend).__name__
    if cls_name == "PostgresBackend":
        async with backend.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM soniq_dead_letter_jobs WHERE id = $1",
                uuid.UUID(dead_letter_id),
            )
            return result.endswith(" 1")
    if cls_name == "SQLiteBackend":
        cursor = await backend._conn.execute(
            "DELETE FROM soniq_dead_letter_jobs WHERE id=?", (dead_letter_id,)
        )
        await backend._conn.commit()
        return bool(cursor.rowcount and cursor.rowcount > 0)
    if cls_name == "MemoryBackend":
        return backend._dead_letter_jobs.pop(dead_letter_id, None) is not None
    raise AssertionError(f"unknown backend: {cls_name}")


async def dlq_count(backend) -> int:
    cls_name = type(backend).__name__
    if cls_name == "PostgresBackend":
        async with backend.acquire() as conn:
            return int(
                await conn.fetchval("SELECT COUNT(*) FROM soniq_dead_letter_jobs")
            )
    if cls_name == "SQLiteBackend":
        async with backend._conn.execute(
            "SELECT COUNT(*) AS c FROM soniq_dead_letter_jobs"
        ) as cursor:
            row = await cursor.fetchone()
        return int(row["c"]) if row is not None else 0
    if cls_name == "MemoryBackend":
        return len(backend._dead_letter_jobs)
    raise AssertionError(f"unknown backend: {cls_name}")


async def dlq_get(backend, dead_letter_id: str):
    cls_name = type(backend).__name__
    if cls_name == "PostgresBackend":
        async with backend.acquire() as conn:
            return await conn.fetchrow(
                "SELECT * FROM soniq_dead_letter_jobs WHERE id = $1",
                uuid.UUID(dead_letter_id),
            )
    if cls_name == "SQLiteBackend":
        async with backend._conn.execute(
            "SELECT * FROM soniq_dead_letter_jobs WHERE id=?", (dead_letter_id,)
        ) as cursor:
            return await cursor.fetchone()
    if cls_name == "MemoryBackend":
        return backend._dead_letter_jobs.get(dead_letter_id)
    raise AssertionError(f"unknown backend: {cls_name}")
