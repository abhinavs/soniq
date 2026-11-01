"""A row whose args Python can't decode must not block the queue.

Postgres accepts JSON that json.loads rejects: an int over 4300 digits, or
nesting deeper than Python's recursion limit. Such a row is dead-lettered
as invalid_arguments and the jobs behind it still run.
"""

import json
import uuid

import asyncpg
import pytest

from soniq import Soniq
from tests.db_utils import TEST_DATABASE_URL

# Python 3.12 and 3.13 can't decode from about 10,000 levels. Postgres
# parses about 18,000 with the default 2MB stack on macOS, but Postgres 15
# on a Linux CI runner already fails at 15,000.
DEPTH = 10500
POISON = {
    "huge_int": '{"x": ' + "9" * 4301 + "}",
    "deep": '{"x": ' + "[" * DEPTH + "]" * DEPTH + "}",
}


def _python_cant_decode(text: str) -> bool:
    try:
        json.loads(text)
    except (ValueError, RecursionError):
        return True
    return False


@pytest.fixture(params=["postgres", "sqlite"])
async def app(request, tmp_path):
    if request.param == "postgres":
        app = Soniq(database_url=TEST_DATABASE_URL)
    else:
        pytest.importorskip("aiosqlite")
        app = Soniq(database_url=f"sqlite:///{tmp_path}/t.db")
    await app.ensure_initialized()
    yield app
    await app.close()


async def _insert_raw(app: Soniq, args_text: str) -> str:
    job_id = str(uuid.uuid4())
    if app.backend.supports_transactional_enqueue:
        async with app.backend.acquire() as conn:
            await conn.execute(
                "INSERT INTO soniq_jobs (id, job_name, args, priority)"
                " VALUES ($1, 'poison.echo', $2::text::jsonb, 0)",
                uuid.UUID(job_id),
                args_text,
            )
    else:
        conn = app.backend._conn
        await conn.execute(
            "INSERT INTO soniq_jobs (id, job_name, args, priority, created_at)"
            " VALUES (?, 'poison.echo', ?, 0, '2000-01-01T00:00:00+00:00')",
            (job_id, args_text),
        )
        await conn.commit()
    return job_id


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", sorted(POISON))
async def test_undecodable_args_are_dead_lettered(app, kind):
    args_text = POISON[kind]
    if not _python_cant_decode(args_text):
        pytest.skip("this Python decodes it")

    @app.job(name="poison.echo")
    async def echo(msg: str = ""):
        return {"msg": msg}

    try:
        poison_id = await _insert_raw(app, args_text)
    except asyncpg.StatementTooComplexError:
        pytest.skip("this Postgres can't store args nested this deep")
    good_id = await app.enqueue(echo, msg="ok")

    # Reading the row degrades to the raw text instead of raising.
    job = await app.get_job(poison_id)
    assert job is not None and isinstance(job["args"], str)

    await app.run_worker(run_once=True)

    assert (await app.get_job(good_id))["status"] == "done"
    dead = await app.get_job(poison_id)
    assert dead["status"] == "dead_letter"
    assert dead["dead_letter_reason"] == "invalid_arguments"
    assert "JSON object" in dead["last_error"]


@pytest.mark.asyncio
async def test_poison_args_survive_dead_lettering_verbatim(soniq_app):
    app = soniq_app
    await app.ensure_initialized()
    poison_id = await _insert_raw(app, POISON["huge_int"])
    await app.backend.mark_job_dead_letter(
        poison_id, attempts=1, error="x", reason="invalid_arguments"
    )
    async with app.backend.acquire() as conn:
        stored = await conn.fetchval(
            "SELECT args::text FROM soniq_dead_letter_jobs WHERE id = $1",
            uuid.UUID(poison_id),
        )
    assert stored == POISON["huge_int"]
    # The dashboard's dead-letter list still loads.
    listed = await app.dashboard_data.get_dead_letter_jobs()
    assert listed["jobs"][0]["args"] == POISON["huge_int"]


@pytest.mark.asyncio
async def test_soniq_enqueue_rejects_deep_args_for_every_job():
    app = Soniq(database_url=TEST_DATABASE_URL)
    await app.setup()
    await app.close()
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        nested = "[" * 500 + "]" * 500
        with pytest.raises(asyncpg.InvalidParameterValueError, match="500"):
            await conn.fetchval(
                "SELECT soniq_enqueue('poison.echo', $1::jsonb)",
                '{"x": ' + nested + "}",
            )
    finally:
        await conn.close()
