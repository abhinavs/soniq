"""The examples shipped with 0.0.5 must still run, unchanged.

Each ``examples/*.py`` file is read from the ``v0.0.5`` git tag (the
published package does not include them), copied to a temp directory, and
run with the code under test. Two examples hardcode a database URL; only
that URL string is swapped for the test database.
"""

import subprocess
import sys
import time
from pathlib import Path

import pytest

from .conftest import cli_env, recreate_db, run_cli

TAG = "v0.0.5"
REPO = Path(__file__).resolve().parents[2]
HARDCODED_URLS = [
    "postgresql://localhost/myapp",
    "postgresql://postgres@localhost/soniq",
]

# Script examples that run to completion.
SCRIPTS = [
    "basic_app.py",
    "file_processing.py",
    "instance_api.py",
    "job_returns_value.py",
    "queue_routing.py",
    "recurring_jobs.py",
    "webhook_delivery.py",
]

# The FastAPI example has no ``__main__``: drive it like a user would.
TRANSACTIONAL_DRIVER = """
from fastapi.testclient import TestClient

from transactional_enqueue import app

with TestClient(app) as client:
    r = client.post("/users", json={"name": "Ada", "email": "ada@example.com"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user_id"] and body["welcome_email_job"], body
    print("created", body)
"""


def _git(*argv):
    return subprocess.run(
        ["git", "-C", str(REPO), *argv], capture_output=True, text=True
    )


@pytest.fixture(scope="module")
def examples(tmp_path_factory):
    if _git("rev-parse", "-q", "--verify", f"refs/tags/{TAG}").returncode:
        # Shallow CI checkouts have no tags.
        _git("fetch", "--depth=1", "origin", f"refs/tags/{TAG}:refs/tags/{TAG}")
    listing = _git("ls-tree", "--name-only", TAG, "examples/")
    assert listing.returncode == 0, f"git tag {TAG} not available: {listing.stderr}"

    url = recreate_db("soniq_compat_examples")
    workdir = tmp_path_factory.mktemp("examples")
    names = [Path(p).name for p in listing.stdout.split() if p.endswith(".py")]
    for name in names:
        text = _git("show", f"{TAG}:examples/{name}").stdout
        for hardcoded in HARDCODED_URLS:
            text = text.replace(f'"{hardcoded}"', f'"{url}"')
        (workdir / name).write_text(text)
    env = cli_env(SONIQ_DATABASE_URL=url)
    assert run_cli("setup", env=env, cwd=workdir).returncode == 0
    return {"dir": workdir, "env": env, "names": names}


def _python(ctx, *argv, timeout=60):
    return subprocess.run(
        [sys.executable, *argv],
        env=ctx["env"],
        cwd=ctx["dir"],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def test_every_example_is_covered(examples):
    covered = set(SCRIPTS) | {"snooze_on_rate_limit.py", "transactional_enqueue.py"}
    assert set(examples["names"]) == covered


@pytest.mark.parametrize("name", SCRIPTS)
def test_script_example_runs(examples, name):
    proc = _python(examples, name)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "Traceback" not in out, out


def test_snooze_example_runs(examples):
    # Runs a worker forever while the job keeps snoozing. Let it go a few
    # seconds and check nothing blew up.
    with open(examples["dir"] / "snooze.log", "w+") as log:
        proc = subprocess.Popen(
            [sys.executable, "snooze_on_rate_limit.py"],
            env=examples["env"],
            cwd=examples["dir"],
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        time.sleep(3)
        still_running = proc.poll() is None
        proc.kill()
        proc.wait()
        log.seek(0)
        out = log.read()
    assert still_running, out
    assert "Traceback" not in out, out


def test_transactional_example_serves_requests(examples):
    (examples["dir"] / "drive_transactional.py").write_text(TRANSACTIONAL_DRIVER)
    proc = _python(examples, "drive_transactional.py")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "created" in out, out
