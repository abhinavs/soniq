"""C1 regression: ``soniq dead-letter replay`` must load job modules.

Before the fix the CLI resolved its app via ``cli_app()`` (empty registry),
so replay always failed with "job <name> not registered" even with
SONIQ_JOBS_MODULES set. It now routes ``replay`` through ``execution_app``
like ``worker``/``scheduler``.
"""

import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests.db_utils import TEST_DATABASE_URL

os.environ.setdefault("SONIQ_DATABASE_URL", TEST_DATABASE_URL)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
JOBS_MODULE = "tests.fixtures.cli_jobs"  # defines @app.job(name="cli_fixture_job")


async def _insert_dlq_row(pool, dlq_id: uuid.UUID) -> None:
    now = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO soniq_dead_letter_jobs (
                id, job_name, args, queue, priority, max_attempts, attempts,
                last_error, dead_letter_reason, original_created_at,
                moved_to_dead_letter_at, resurrection_count
            ) VALUES ($1, 'cli_fixture_job', '{"message": "hi"}', 'default',
                      100, 3, 3, 'boom', 'max_retries_exceeded', $2, $2, 0)
            """,
            dlq_id,
            now,
        )


def _run_replay(dlq_id: str, *extra_args: str):
    env = os.environ.copy()
    env["SONIQ_DATABASE_URL"] = TEST_DATABASE_URL
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    env.pop("SONIQ_JOBS_MODULES", None)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "soniq.cli.main",
            "dead-letter",
            "replay",
            dlq_id,
            *extra_args,
        ],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(PROJECT_ROOT),
        env=env,
    )


@pytest.mark.asyncio
async def test_replay_with_jobs_modules_succeeds(soniq_app):
    pool = await soniq_app._get_pool()
    dlq_id = uuid.uuid4()
    await _insert_dlq_row(pool, dlq_id)

    result = _run_replay(str(dlq_id), "--jobs-modules", JOBS_MODULE)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"

    async with pool.acquire() as conn:
        new_rows = await conn.fetch(
            "SELECT id FROM soniq_jobs WHERE job_name = 'cli_fixture_job'"
        )
        assert len(new_rows) == 1
        assert str(new_rows[0]["id"]) != str(dlq_id)  # fresh UUID
        resurrection_count = await conn.fetchval(
            "SELECT resurrection_count FROM soniq_dead_letter_jobs WHERE id = $1",
            dlq_id,
        )
        assert resurrection_count == 1


@pytest.mark.asyncio
async def test_replay_without_jobs_modules_errors_clearly(soniq_app):
    pool = await soniq_app._get_pool()
    dlq_id = uuid.uuid4()
    await _insert_dlq_row(pool, dlq_id)

    result = _run_replay(str(dlq_id))
    assert result.returncode == 1
    assert "job modules" in result.stderr.lower()
