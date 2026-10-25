"""Migration 0006: soniq_enqueue() and soniq_get_job() for producers in any
language, the dead-letter tags fix, and enqueue dedup when a job is both
unique and has a dedup_key.
"""

import json
import uuid
from pathlib import Path

import asyncpg
import pytest

from soniq import Soniq
from soniq.utils.hashing import compute_args_hash
from tests.db_utils import TEST_DATABASE_URL

MIGRATION = (
    Path(__file__).parents[2] / "soniq/backends/postgres/migrations/0006_sql_api.sql"
)

# No floats: languages print them differently, and the docs say so.
HASH_VECTORS = [
    {"to": "zoë@example.com", "order_id": 42, "tags": ["a", "b"]},
    {},
    {"a": [], "b": {}},
    {"n": None, "t": True, "f": False},
    {"b": 1, "a": 2, "aa": 3, "B": 4, "_": 5, "é": 6, "z": 7},
    {"s": 'quote" back\\ slash/ nl\n cr\r tab\t bs\b ff\f'},
    {"s": "\x01\x1f\x7f"},
    {"s": "😀 中文"},
    {"😀": {"ключ": [1, -2, 0]}},
    {"big": 12345678901234567890},
    {"deep": [[{"y": 1, "x": [True, None]}]]},
]


@pytest.fixture
async def app():
    a = Soniq(database_url=TEST_DATABASE_URL)

    @a.job(name="sqlapi.send", unique=True)
    async def send(n: int):
        return {"n": n}

    await a.setup()
    yield a
    await a._backend.reset()
    await a.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("args", HASH_VECTORS)
async def test_sql_hash_matches_python(app, args):
    async with app.backend.acquire() as conn:
        sql = await conn.fetchval(
            "SELECT soniq_args_hash($1::text::jsonb)", json.dumps(args)
        )
    assert sql == compute_args_hash(args)


@pytest.mark.asyncio
async def test_sql_enqueue_runs_and_reads_back(app):
    async with app.backend.acquire() as conn:
        job_id = await conn.fetchval(
            "SELECT soniq_enqueue('sqlapi.send', '{\"n\": 7}')"
        )
        row = await conn.fetchrow("SELECT * FROM soniq_get_job($1)", job_id)
    assert row["status"] == "queued"
    assert row["max_attempts"] == 4
    assert row["result"] is None

    await app.run_worker(run_once=True)
    async with app.backend.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM soniq_get_job($1)", job_id)
    assert row["status"] == "done"
    assert row["result"] == {"n": 7}


@pytest.mark.asyncio
async def test_sql_and_python_unique_jobs_dedup_against_each_other(app):
    py_id = await app.enqueue("sqlapi.send", args={"n": 1}, unique=True)
    async with app.backend.acquire() as conn:
        sql_id = await conn.fetchval(
            "SELECT soniq_enqueue('sqlapi.send', '{\"n\": 1}', unique_job => true)"
        )
        again = await conn.fetchval(
            "SELECT soniq_enqueue('sqlapi.send', '{\"n\": 1}', unique_job => true)"
        )
    assert str(sql_id) == str(again) == py_id


@pytest.mark.asyncio
async def test_sql_enqueue_rolls_back_with_the_transaction(app):
    async with app.backend.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        job_id = await conn.fetchval("SELECT soniq_enqueue('sqlapi.send')")
        await tr.rollback()
        assert await conn.fetchrow("SELECT * FROM soniq_get_job($1)", job_id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "call, hint",
    [
        ("soniq_enqueue('Not A Name')", "SONIQ_INVALID_TASK_NAME"),
        ("soniq_enqueue('sqlapi.send', '[1]')", "SONIQ_TASK_ARGS_INVALID"),
    ],
)
async def test_sql_enqueue_rejects_bad_input(app, call, hint):
    async with app.backend.acquire() as conn:
        with pytest.raises(asyncpg.InvalidParameterValueError) as exc:
            await conn.fetchval(f"SELECT {call}")
    assert exc.value.hint == hint


@pytest.mark.asyncio
async def test_get_job_finds_dead_lettered_jobs(app):
    job_id = uuid.uuid4()
    async with app.backend.acquire() as conn:
        await conn.execute(
            "INSERT INTO soniq_dead_letter_jobs (id, job_name, args, queue, priority,"
            " max_attempts, attempts, last_error, dead_letter_reason,"
            " original_created_at, moved_to_dead_letter_at)"
            " VALUES ($1, 'sqlapi.send', '{}', 'default', 100, 1, 1, 'boom',"
            " 'max_retries_exceeded', NOW(), NOW())",
            job_id,
        )
        row = await conn.fetchrow("SELECT * FROM soniq_get_job($1)", job_id)
    assert row["status"] == "dead_letter"
    assert row["dead_letter_reason"] == "max_retries_exceeded"


@pytest.mark.asyncio
async def test_double_encoded_dead_letter_tags_are_fixed_and_readable(app):
    job_id = uuid.uuid4()
    async with app.backend.acquire() as conn:
        # How 0.1.0 stored tags: a JSON string holding the object.
        await conn.execute(
            "INSERT INTO soniq_dead_letter_jobs (id, job_name, args, queue, priority,"
            " max_attempts, attempts, last_error, dead_letter_reason,"
            " original_created_at, moved_to_dead_letter_at, tags)"
            " VALUES ($1, 'sqlapi.send', '{}', 'default', 100, 1, 1, 'boom',"
            " 'max_retries_exceeded', NOW(), NOW(), to_jsonb($2::text))",
            job_id,
            '{"team": "billing"}',
        )
    # A 0.1.0 worker can still write these, so the reader copes...
    dead = await app.dead_letter.get_dead_letter_job(str(job_id))
    assert dead.tags == {"team": "billing"}

    # ...and the migration's UPDATE turns them back into objects.
    fix = MIGRATION.read_text().split("UPDATE soniq_dead_letter_jobs", 1)[1]
    async with app.backend.acquire() as conn:
        await conn.execute("UPDATE soniq_dead_letter_jobs" + fix)
        kind = await conn.fetchval(
            "SELECT jsonb_typeof(tags) FROM soniq_dead_letter_jobs WHERE id = $1",
            job_id,
        )
    assert kind == "object"


@pytest.mark.asyncio
async def test_unique_job_with_dedup_key_returns_existing_id(app):
    # The second enqueue clashes on the unique index, not the dedup_key one.
    first = await app.enqueue("sqlapi.send", args={"n": 5}, dedup_key="a")
    second = await app.enqueue("sqlapi.send", args={"n": 5}, dedup_key="b")
    third = await app.enqueue("sqlapi.send", args={"n": 6}, dedup_key="a")
    assert first == second == third


@pytest.mark.asyncio
async def test_unique_job_with_dedup_key_on_psycopg(app):
    psycopg = pytest.importorskip("psycopg")
    async with await psycopg.AsyncConnection.connect(TEST_DATABASE_URL) as conn:
        first = await app.enqueue(
            "sqlapi.send", connection=conn, args={"n": 5}, dedup_key="a"
        )
        second = await app.enqueue(
            "sqlapi.send", connection=conn, args={"n": 5}, dedup_key="b"
        )
        await conn.commit()
    assert first == second


async def _insert_null_max_attempts(app, job_name):
    job_id = uuid.uuid4()
    async with app.backend.acquire() as conn:
        await conn.execute(
            "INSERT INTO soniq_jobs (id, job_name, args, max_attempts)"
            " VALUES ($1, $2, '{}', NULL)",
            job_id,
            job_name,
        )
    return job_id


@pytest.mark.asyncio
async def test_null_max_attempts_uses_the_handlers_retries(app):
    @app.job(name="sqlapi.flaky", retries=0)
    async def flaky():
        raise RuntimeError("boom")

    job_id = await _insert_null_max_attempts(app, "sqlapi.flaky")
    await app.run_worker(run_once=True)

    # retries=0 means one attempt. The default of 3 retries would requeue it.
    async with app.backend.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM soniq_get_job($1)", job_id)
    assert row["status"] == "dead_letter"
    assert row["max_attempts"] == 1


@pytest.mark.asyncio
async def test_null_max_attempts_on_an_unknown_job_still_dead_letters(app):
    job_id = await _insert_null_max_attempts(app, "sqlapi.nobody")
    await app.run_worker(run_once=True)

    async with app.backend.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM soniq_get_job($1)", job_id)
    assert row["status"] == "dead_letter"
    assert row["dead_letter_reason"] == "job_not_found"


@pytest.mark.asyncio
async def test_sql_enqueue_with_null_priority_can_dead_letter(app):
    @app.job(name="sqlapi.fails", retries=0)
    async def fails():
        raise RuntimeError("boom")

    async with app.backend.acquire() as conn:
        job_id = await conn.fetchval(
            "SELECT soniq_enqueue('sqlapi.fails', priority => NULL)"
        )
    await app.run_worker(run_once=True)

    async with app.backend.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM soniq_get_job($1)", job_id)
    assert row["status"] == "dead_letter"


@pytest.mark.asyncio
async def test_session_setting_cannot_loosen_the_name_check(app):
    async with app.backend.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SET LOCAL soniq.task_name_pattern = '.*'")
            with pytest.raises(asyncpg.InvalidParameterValueError):
                await conn.fetchval("SELECT soniq_enqueue('Not A Name')")


@pytest.mark.asyncio
async def test_args_hash_works_off_the_soniq_search_path(app):
    async with app.backend.acquire() as conn:
        schema = await conn.fetchval("SELECT current_schema()")
        async with conn.transaction():
            await conn.execute("SET LOCAL search_path = pg_catalog")
            got = await conn.fetchval(
                f"SELECT {schema}.soniq_args_hash('{{\"a\": 1}}'::jsonb)"
            )
    assert got == compute_args_hash({"a": 1})


async def _stranded_job(app, worker_status, hostname="box", pid=1):
    worker_id, job_id = uuid.uuid4(), uuid.uuid4()
    async with app.backend.acquire() as conn:
        await conn.execute(
            "INSERT INTO soniq_workers (id, hostname, pid, queues, concurrency,"
            " status, started_at, last_heartbeat)"
            " VALUES ($1, $2, $3, '{default}', 1, $4, NOW(), NOW())",
            worker_id,
            hostname,
            pid,
            worker_status,
        )
        await conn.execute(
            "INSERT INTO soniq_jobs (id, job_name, args, status, worker_id)"
            " VALUES ($1, 'sqlapi.send', '{\"n\": 1}', 'processing', $2)",
            job_id,
            worker_id,
        )
    return job_id


async def _status(app, job_id):
    async with app.backend.acquire() as conn:
        return await conn.fetchval(
            "SELECT status FROM soniq_jobs WHERE id = $1", job_id
        )


@pytest.mark.asyncio
async def test_cleanup_requeues_jobs_held_by_a_stopped_worker(app):
    # A job whose final write failed stays 'processing' after its worker exits.
    stranded = await _stranded_job(app, "stopped")
    running = await _stranded_job(app, "active", hostname="other")

    await app.backend.cleanup_stale_workers(stale_threshold_seconds=300)

    assert await _status(app, stranded) == "queued"
    assert await _status(app, running) == "processing"


@pytest.mark.asyncio
async def test_worker_restarted_with_the_same_pid_can_register(app):
    job_id = await _stranded_job(app, "active")
    async with app.backend.acquire() as conn:
        # A finished job keeps its worker_id, which used to break the restart.
        await conn.execute(
            "INSERT INTO soniq_jobs (id, job_name, args, status, worker_id)"
            " SELECT $1, job_name, args, 'done', worker_id FROM soniq_jobs"
            " WHERE id = $2",
            uuid.uuid4(),
            job_id,
        )

    await app.backend.register_worker(
        worker_id=str(uuid.uuid4()),
        hostname="box",
        pid=1,
        queues=["default"],
        concurrency=1,
    )
    assert await _status(app, job_id) == "queued"


@pytest.mark.asyncio
async def test_second_worker_in_the_same_process_leaves_jobs_alone(app):
    first = str(uuid.uuid4())
    kwargs = dict(hostname="box", pid=2, queues=["default"], concurrency=1)
    await app.backend.register_worker(worker_id=first, **kwargs)
    job_id = uuid.uuid4()
    async with app.backend.acquire() as conn:
        await conn.execute(
            "INSERT INTO soniq_jobs (id, job_name, args, status, worker_id)"
            " VALUES ($1, 'sqlapi.send', '{\"n\": 1}', 'processing', NULL)",
            job_id,
        )
        await conn.execute(
            "UPDATE soniq_jobs SET worker_id = $1 WHERE id = $2",
            uuid.UUID(first),
            job_id,
        )
    await app.backend.update_heartbeat(first)

    # The first worker is alive and mid-job, so its job must not be requeued.
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await app.backend.register_worker(worker_id=str(uuid.uuid4()), **kwargs)
    assert await _status(app, job_id) == "processing"
