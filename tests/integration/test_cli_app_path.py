"""``soniq setup APP`` / ``soniq worker APP``: the app path alone is enough.

The worker runs in a subprocess with every ``SONIQ_*`` variable removed, so the
database URL and the job registry can only come from the instance named on the
command line.
"""

import os
import subprocess
import sys

import pytest

from soniq import task_ref
from tests.db_utils import TEST_DATABASE_URL

APP_PATH_JOB = task_ref(name="app_path_job")

APP_MODULE = """
from soniq import Soniq

soniq = Soniq(database_url={url!r})


@soniq.job(name="app_path_job")
async def app_path_job(message: str = "hi"):
    return f"done:{{message}}"
"""


def _soniq(tmp_path, *argv):
    env = {k: v for k, v in os.environ.items() if not k.startswith("SONIQ_")}
    return subprocess.run(
        [sys.executable, "-m", "soniq.cli.main", *argv],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(tmp_path),
        env=env,
    )


@pytest.fixture
def app_module(tmp_path):
    (tmp_path / "app_path_mod.py").write_text(APP_MODULE.format(url=TEST_DATABASE_URL))
    return tmp_path


@pytest.mark.asyncio
async def test_setup_and_worker_run_from_app_path(soniq_app, app_module):
    setup = _soniq(app_module, "setup", "app_path_mod:soniq")
    assert setup.returncode == 0, setup.stdout + setup.stderr

    job_id = await soniq_app.enqueue(APP_PATH_JOB, args={"message": "x"})

    worker = _soniq(app_module, "worker", "app_path_mod:soniq", "--run-once")
    assert worker.returncode == 0, worker.stdout + worker.stderr

    job = await soniq_app.get_job(job_id)
    assert job["status"] == "done", job
    assert job["result"] == "done:x"


@pytest.mark.asyncio
async def test_worker_accepts_module_only_app_flag(soniq_app, app_module):
    job_id = await soniq_app.enqueue(APP_PATH_JOB, args={"message": "y"})

    worker = _soniq(app_module, "worker", "--app", "app_path_mod", "--run-once")
    assert worker.returncode == 0, worker.stdout + worker.stderr

    job = await soniq_app.get_job(job_id)
    assert job["status"] == "done", job


def test_unknown_attribute_fails_clearly(app_module):
    proc = _soniq(app_module, "worker", "app_path_mod:nope", "--run-once")
    assert proc.returncode == 1
    assert "has no attribute 'nope'" in proc.stderr
