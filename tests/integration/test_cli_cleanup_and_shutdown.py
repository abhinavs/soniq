"""CLI regressions: ``dead-letter cleanup --dry-run`` and scheduler SIGTERM."""

import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from tests.integration.conftest import _TEST_DATABASE_URL

ROOT = Path(__file__).resolve().parents[2]


def _cli(*argv):
    return subprocess.run(
        [sys.executable, "-m", "soniq.cli.main", *argv],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )


async def _dlq_count(app):
    async with app.backend.acquire() as conn:
        return await conn.fetchval("SELECT COUNT(*) FROM soniq_dead_letter_jobs")


@pytest.mark.asyncio
async def test_dead_letter_cleanup_dry_run_deletes_nothing(soniq_app):
    await soniq_app.ensure_initialized()
    job_id = str(uuid.uuid4())
    await soniq_app.backend.create_job(
        job_id=job_id,
        job_name="test.cleanup",
        args={},
        args_hash=None,
        max_attempts=1,
        priority=100,
        queue="default",
        unique=False,
    )
    await soniq_app.backend.mark_job_dead_letter(
        job_id, attempts=1, error="boom", reason="max_retries_exceeded", tags={}
    )
    url = ["--database-url", _TEST_DATABASE_URL]

    dry = _cli("dead-letter", "cleanup", "--days", "0", "--dry-run", *url)
    assert dry.returncode == 0, dry.stderr
    assert "Dry run: would remove 1 dead-letter job(s)" in dry.stdout
    assert await _dlq_count(soniq_app) == 1

    real = _cli("dead-letter", "cleanup", "--days", "0", *url)
    assert real.returncode == 0, real.stderr
    assert "Removed 1 dead-letter job(s)" in real.stdout
    assert await _dlq_count(soniq_app) == 0


def test_scheduler_stops_gracefully_on_sigterm(tmp_path):
    log = tmp_path / "scheduler.log"
    with open(log, "w+") as out:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "soniq.cli.main",
                "scheduler",
                "--database-url",
                _TEST_DATABASE_URL,
            ],
            cwd=ROOT,
            env={**os.environ, "SONIQ_JOBS_MODULES": "tests.fixtures.cli_jobs"},
            stdout=out,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            deadline = time.monotonic() + 30
            while "Scheduler started" not in log.read_text():
                assert proc.poll() is None, log.read_text()
                assert time.monotonic() < deadline, log.read_text()
                time.sleep(0.1)
        finally:
            proc.send_signal(signal.SIGTERM)
            rc = proc.wait(timeout=30)

    output = log.read_text()
    assert rc == 0, output
    assert "Scheduler stopped" in output
