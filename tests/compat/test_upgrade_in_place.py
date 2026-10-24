"""Upgrading in place from soniq 0.0.5 must just work.

soniq 0.0.5 is installed from PyPI into its own virtualenv. That install
sets up the database and leaves behind a normal job, a scheduled job, a
unique job, a recurring schedule and a dead-lettered job. Then the code
under test takes over: ``soniq setup`` must have nothing to apply, the
scheduler must keep firing the old schedule, the worker must finish every
old job, and the dead-lettered job must still be in the dead-letter queue.

The 0.0.5 virtualenv is cached in pytest's cache directory, so only the
first run needs the network.
"""

import subprocess
import time

from .conftest import check, cli_env, fetch, old_venv, recreate_db, run_cli, run_until

OLD_VERSION = "0.0.5"

JOBS = """
from datetime import timedelta

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

# Runs under soniq 0.0.5.
OLD_PRODUCER = """
import asyncio
from datetime import datetime, timedelta, timezone

from compat_upgrade_jobs import boom, hello, soniq, tick


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


def test_upgrade_in_place(request, tmp_path):
    url = recreate_db("soniq_compat_upgrade")
    old = old_venv(request.config.cache.mkdir("soniq-0.0.5-venv"), OLD_VERSION)
    (tmp_path / "compat_upgrade_jobs.py").write_text(JOBS.replace("{url}", url))
    (tmp_path / "produce.py").write_text(OLD_PRODUCER)
    env = cli_env(SONIQ_DATABASE_URL=url, SONIQ_JOBS_MODULES="compat_upgrade_jobs")

    # cwd is tmp_path, so the old interpreter cannot import this checkout.
    def old_run(*argv):
        return subprocess.run(
            argv, env=env, cwd=tmp_path, capture_output=True, text=True, timeout=60
        )

    check(old_run(str(old / "bin" / "soniq"), "setup"), "Applying")
    out = check(old_run(str(old / "bin" / "python"), "produce.py"), "DEAD ")
    dead_id = out.split("DEAD ")[1].split()[0]

    jobs = fetch(url, "SELECT job_name, status FROM soniq_jobs")
    assert sorted(r["job_name"] for r in jobs) == ["compat.hello"] * 3
    assert {r["status"] for r in jobs} == {"queued"}

    # --- the code under test takes over ---
    check(
        run_cli("setup", env=env, cwd=tmp_path),
        "Core schema is already up to date",
        "Database setup completed (no migrations needed)",
    )
    check(
        run_cli("migrate-status", env=env, cwd=tmp_path),
        "Pending migrations: 0",
        "Status: Up to date",
    )

    def ticked(_out):
        rows = fetch(url, "SELECT 1 FROM soniq_jobs WHERE job_name = 'compat.tick'")
        return bool(rows)

    rc, out = run_until(
        ["scheduler", "--check-interval", "1"], env=env, cwd=tmp_path, ready=ticked
    )
    assert rc == 0, out

    # The scheduled job was due 2s after the producer ran.
    time.sleep(2)
    check(
        run_cli("worker", "--run-once", env=env, cwd=tmp_path),
        "hello normal",
        "hello scheduled",
        "hello unique",
    )

    rows = fetch(url, "SELECT job_name, status FROM soniq_jobs")
    left = [(r["job_name"], r["status"]) for r in rows if r["status"] != "done"]
    assert not left, f"jobs not finished after upgrade: {left}"
    dead = fetch(url, "SELECT id::text AS id, job_name FROM soniq_dead_letter_jobs")
    assert [(r["id"], r["job_name"]) for r in dead] == [(dead_id, "compat.boom")]
    check(run_cli("dead-letter", "list", env=env, cwd=tmp_path), dead_id)
