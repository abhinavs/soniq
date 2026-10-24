"""Format 1 on Postgres: new rows are plain JSON, and rows that 0.1.0 left
behind (stringified datetimes, ``str()`` results) are still processed.
"""

import json
import uuid
from datetime import datetime, timezone

import pytest

from soniq import Soniq
from soniq.core.processor import process_job_via_backend
from tests.db_utils import TEST_DATABASE_URL


@pytest.fixture
async def app():
    a = Soniq(database_url=TEST_DATABASE_URL)
    await a.setup()
    yield a
    await a._backend.reset()
    await a.close()


async def _process_one(app):
    return await process_job_via_backend(
        backend=app._backend,
        job_registry=app._get_job_registry(),
        queues=["default"],
    )


async def _insert_legacy_job(app, job_name, args):
    # Written with raw SQL, the way 0.1.0's default=str encoder left it.
    job_id = uuid.uuid4()
    async with app.backend.acquire() as conn:
        await conn.execute(
            "INSERT INTO soniq_jobs (id, job_name, args, status, queue, scheduled_at)"
            " VALUES ($1, $2, $3::text::jsonb, 'queued', 'default', NOW())",
            job_id,
            job_name,
            json.dumps(args),
        )
    return str(job_id)


@pytest.mark.asyncio
async def test_legacy_stringified_args_are_still_processed(app):
    seen = []

    @app.job(name="format.legacy")
    async def legacy(when: str):
        seen.append(when)
        return {"ok": True}

    job_id = await _insert_legacy_job(
        app, "format.legacy", {"when": "2024-01-01 00:00:00+00:00"}
    )
    assert await _process_one(app) is True
    assert seen == ["2024-01-01 00:00:00+00:00"]
    assert await app.get_result(job_id) == {"ok": True}


@pytest.mark.asyncio
async def test_legacy_str_result_reads_back_as_a_string(app):
    job_id = await _insert_legacy_job(app, "format.old_result", {})
    async with app.backend.acquire() as conn:
        await conn.execute(
            "UPDATE soniq_jobs SET status = 'done', result = $2::text::jsonb,"
            " expires_at = NOW() + interval '1 hour' WHERE id = $1",
            uuid.UUID(job_id),
            json.dumps("User(id=1)"),
        )
    job = await app.get_job(job_id)
    assert job["result"] == "User(id=1)"


@pytest.mark.asyncio
async def test_non_json_result_is_null_on_postgres(app):
    @app.job(name="format.clock")
    async def clock():
        return datetime(2024, 1, 1, tzinfo=timezone.utc)

    job_id = await app.enqueue(clock)
    assert await _process_one(app) is True
    job = await app.get_job(job_id)
    assert job["status"] == "done"
    assert job["result"] is None


@pytest.mark.asyncio
async def test_dead_letter_tags_are_an_object_and_legacy_rows_replay(app):
    @app.job(name="format.dlq")
    async def dlq(when: str):
        return when

    job_id = await _insert_legacy_job(
        app, "format.dlq", {"when": "2024-01-01 00:00:00+00:00"}
    )
    await app._backend.mark_job_dead_letter(
        job_id, attempts=3, error="boom", reason="max_retries", tags={"team": "a"}
    )
    async with app.backend.acquire() as conn:
        kind = await conn.fetchval(
            "SELECT jsonb_typeof(tags) FROM soniq_dead_letter_jobs WHERE id = $1",
            uuid.UUID(job_id),
        )
    assert kind == "object"

    new_id = await app.dead_letter.replay(job_id)
    assert await _process_one(app) is True
    assert await app.get_result(new_id) == "2024-01-01 00:00:00+00:00"
