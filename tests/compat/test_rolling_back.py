"""soniq 0.1.0 workers must keep running rows that the current code writes.

During a rolling deploy, and after a rollback, old workers read what new
producers wrote. The current code sets up the database, enqueues a normal
job, a scheduled job, a unique job and a recurring schedule, and leaves a
dead-lettered job. Then soniq 0.1.0 from PyPI takes over: its scheduler
must fire the schedule, its worker must finish every job, and its
dead-letter commands must see the dead-lettered job.

The 0.1.0 virtualenv is cached in pytest's cache directory, so only the
first run needs the network.
"""

import subprocess
import sys
import time

from .conftest import check, cli_env, fetch, old_venv, recreate_db, run_cli

OLD_VERSION = "0.1.0"

JOBS = """
from soniq import Soniq

soniq = Soniq(database_url="{url}")


@soniq.job(name="compat.hello")
async def hello(to: str):
    print(f"hello {to}", flush=True)


@soniq.job(name="compat.boom", retries=0, queue="compat_dlq")
async def boom():
    raise RuntimeError("boom")


@soniq.job(name="compat.tick")
async def tick():
    pass
"""

# Runs under the code being tested.
NEW_PRODUCER = """
import asyncio
from datetime import datetime, timedelta, timezone

from compat_back_jobs import boom, hello, soniq, tick


async def main():
    await soniq.enqueue(hello, to="normal")
    await soniq.enqueue(
        hello,
        to="scheduled",
        scheduled_at=datetime.now(timezone.utc) + timedelta(seconds=2),
    )
    await soniq.enqueue(hello, to="unique", unique=True)
    await soniq.enqueue(hello, to="unique", unique=True)
    await soniq.scheduler.add(tick, every=timedelta(seconds=1), name="compat.tick")
    dead = await soniq.enqueue(boom)
    await soniq.run_worker(run_once=True, queues=["compat_dlq"])
    await soniq.close()
    print("DEAD", dead)


asyncio.run(main())
"""


def test_old_worker_runs_new_rows(request, tmp_path):
    url = recreate_db("soniq_compat_back")
    old = old_venv(request.config.cache.mkdir("soniq-0.1.0-venv"), OLD_VERSION)
    (tmp_path / "compat_back_jobs.py").write_text(JOBS.replace("{url}", url))
    (tmp_path / "produce.py").write_text(NEW_PRODUCER)
    env = cli_env(SONIQ_DATABASE_URL=url, SONIQ_JOBS_MODULES="compat_back_jobs")

    # cwd is tmp_path, so the old interpreter cannot import this checkout.
    def old_run(*argv, timeout=60):
        return subprocess.run(
            [str(old / "bin" / "soniq"), *argv],
            env=env,
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    check(run_cli("setup", env=env, cwd=tmp_path))
    out = check(
        subprocess.run(
            [sys.executable, "produce.py"],
            env=env,
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=60,
        ),
        "DEAD ",
    )
    dead_id = out.split("DEAD ")[1].split()[0]

    # --- soniq 0.1.0 takes over ---
    check(old_run("migrate-status"), "Pending migrations: 0")

    scheduler = subprocess.Popen(
        [str(old / "bin" / "soniq"), "scheduler", "--check-interval", "1"],
        env=env,
        cwd=tmp_path,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 30
        while not fetch(url, "SELECT 1 FROM soniq_jobs WHERE job_name='compat.tick'"):
            assert scheduler.poll() is None, "old scheduler exited early"
            assert time.monotonic() < deadline, "old scheduler never fired"
            time.sleep(0.2)
    finally:
        scheduler.terminate()
        scheduler.wait(timeout=30)

    # The scheduled job was due 2s after the producer ran.
    time.sleep(2)
    check(
        old_run("worker", "--run-once"),
        "hello normal",
        "hello scheduled",
        "hello unique",
    )

    rows = fetch(url, "SELECT job_name, status FROM soniq_jobs")
    left = [(r["job_name"], r["status"]) for r in rows if r["status"] != "done"]
    assert not left, f"jobs not finished by the old worker: {left}"
    hellos = [r for r in rows if r["job_name"] == "compat.hello"]
    assert len(hellos) == 3
    check(old_run("dead-letter", "list"), dead_id)
