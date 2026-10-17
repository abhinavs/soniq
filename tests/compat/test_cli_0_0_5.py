"""Every CLI form shown in the 0.0.5 README, quickstart and CLI reference
must still work and print the same key lines.

The tests in this module share one database and run in file order: setup,
then workers, then the read-only commands, then the dead-letter actions.
Long-running commands (``scheduler``, ``dashboard``) are started, checked
once they are up, and stopped with Ctrl+C.
"""

import asyncio
import json
import socket
import urllib.request

import asyncpg
import pytest

from soniq import Soniq

from .conftest import cli_env, recreate_db, run_cli, run_until

JOBS = """
from datetime import timedelta

from soniq import Soniq

soniq = Soniq(database_url="{url}")


@soniq.job(name="compat.hello")
async def hello(to: str):
    print(f"hello {to}", flush=True)


@soniq.job(name="compat.boom", retries=0)
async def boom():
    raise RuntimeError("boom")


@soniq.periodic(every=timedelta(hours=1), name="compat.tick")
async def tick():
    pass
"""

STUBS = """
from soniq import task_ref

hello = task_ref(name="compat.hello")
boom = task_ref(name="compat.boom")
tick = task_ref(name="compat.tick")
"""


@pytest.fixture(scope="module")
def ctx(tmp_path_factory):
    url = recreate_db("soniq_compat_cli")
    workdir = tmp_path_factory.mktemp("cli")
    (workdir / "compat_jobs.py").write_text(JOBS.replace("{url}", url))
    (workdir / "compat_stubs").mkdir()
    (workdir / "compat_stubs" / "__init__.py").write_text(STUBS)
    return {"url": url, "dir": workdir}


def soniq_cmd(ctx, *argv, url_env=True, jobs_env=False, expect=(), rc=0):
    """Run ``soniq *argv`` and check the exit code and the key output lines."""
    extra = {}
    if url_env:
        extra["SONIQ_DATABASE_URL"] = ctx["url"]
    if jobs_env:
        extra["SONIQ_JOBS_MODULES"] = "compat_jobs"
    proc = run_cli(*argv, env=cli_env(**extra), cwd=ctx["dir"])
    out = proc.stdout + proc.stderr
    assert proc.returncode == rc, f"soniq {' '.join(argv)} -> {proc.returncode}\n{out}"
    for line in expect:
        assert line in out, f"{line!r} missing from `soniq {' '.join(argv)}`:\n{out}"
    return out


def enqueue(ctx, *jobs):
    async def go():
        soniq = Soniq(database_url=ctx["url"], enqueue_validation="none")
        try:
            return [await soniq.enqueue(name, **kw) for name, kw in jobs]
        finally:
            await soniq.close()

    return asyncio.run(go())


def dead_letter_ids(ctx):
    async def go():
        conn = await asyncpg.connect(ctx["url"])
        try:
            rows = await conn.fetch("SELECT id FROM soniq_dead_letter_jobs")
        finally:
            await conn.close()
        return sorted(str(r["id"]) for r in rows)

    return asyncio.run(go())


def make_dead_letters(ctx, n):
    enqueue(ctx, *[("compat.boom", {})] * n)
    soniq_cmd(ctx, "worker", "--run-once", jobs_env=True)
    assert len(dead_letter_ids(ctx)) >= n


# --- setup / migrations ------------------------------------------------------


def test_setup_with_env_url(ctx):
    soniq_cmd(ctx, "setup", expect=["Found 5 core migrations", "Applying"])


def test_setup_with_database_url_flag(ctx):
    soniq_cmd(
        ctx,
        "setup",
        "--database-url",
        ctx["url"],
        url_env=False,
        expect=[
            "Core schema is already up to date",
            "Database setup completed (no migrations needed)",
        ],
    )


def test_migrate_status(ctx):
    soniq_cmd(
        ctx,
        "migrate-status",
        expect=[
            "Total migrations:",
            "Applied migrations:",
            "Pending migrations: 0",
            "Status: Up to date",
        ],
    )


# --- worker --------------------------------------------------------------------


def test_worker_env_style(ctx):
    # README: SONIQ_JOBS_MODULES=jobs soniq worker --concurrency 4
    enqueue(ctx, ("compat.hello", {"args": {"to": "env"}}))
    soniq_cmd(
        ctx,
        "worker",
        "--concurrency",
        "4",
        "--run-once",
        jobs_env=True,
        expect=["Discovering jobs in: compat_jobs", "hello env"],
    )


def test_worker_flags_style(ctx):
    # CLI reference: --jobs-modules, --queues, --database-url
    enqueue(
        ctx,
        ("compat.hello", {"args": {"to": "urgent"}, "queue": "urgent"}),
        ("compat.hello", {"args": {"to": "default"}}),
    )
    soniq_cmd(
        ctx,
        "worker",
        "--concurrency",
        "8",
        "--queues",
        "urgent,default",
        "--jobs-modules",
        "compat_jobs",
        "--database-url",
        ctx["url"],
        "--run-once",
        url_env=False,
        expect=["hello urgent", "hello default"],
    )


def test_worker_without_jobs_modules_fails(ctx):
    soniq_cmd(ctx, "worker", "--run-once", rc=1, expect=["SONIQ_JOBS_MODULES"])


# --- read-only commands --------------------------------------------------------


def test_status(ctx):
    make_dead_letters(ctx, 1)
    soniq_cmd(ctx, "status", expect=["Soniq System Status", "Database connection: OK"])
    soniq_cmd(
        ctx,
        "status",
        "--verbose",
        "--jobs",
        expect=[
            "Queue Statistics:",
            "Total Jobs:",
            "Queued:",
            "Processing:",
            "Done:",
            "Cancelled:",
            "Dead Letter:",
            "Recent Jobs",
        ],
    )


@pytest.mark.parametrize("flags", [[], ["--stale"], ["--cleanup"], ["--schedules"]])
def test_inspect(ctx, flags):
    soniq_cmd(
        ctx,
        "inspect",
        *flags,
        expect=["Soniq Worker Status", "Health:", "Recurring Schedules:"],
    )


def test_tasks_check(ctx):
    soniq_cmd(ctx, "tasks-check", str(ctx["dir"] / "compat_stubs"), expect=["OK"])


# --- dead-letter -----------------------------------------------------------------


def test_dead_letter_list(ctx):
    ids = dead_letter_ids(ctx)
    soniq_cmd(ctx, "dead-letter", "list", expect=[ids[0], "compat.boom"])
    soniq_cmd(
        ctx,
        "dead-letter",
        "list",
        "--limit",
        "50",
        "--filter",
        "compat.boom",
        expect=[ids[0]],
    )


@pytest.mark.parametrize("fmt", ["json", "csv"])
def test_dead_letter_export(ctx, fmt):
    target = ctx["dir"] / f"dead_letter.{fmt}"
    soniq_cmd(
        ctx,
        "dead-letter",
        "export",
        "--format",
        fmt,
        "--output",
        str(target),
        expect=[str(target)],
    )
    text = target.read_text()
    assert "compat.boom" in text
    if fmt == "json":
        json.loads(text)


def test_dead_letter_replay_one(ctx):
    dead = dead_letter_ids(ctx)[0]
    soniq_cmd(
        ctx,
        "dead-letter",
        "replay",
        dead,
        "--jobs-modules",
        "compat_jobs",
        expect=[f"Replayed {dead} as "],
    )


def test_dead_letter_replay_all(ctx):
    count = len(dead_letter_ids(ctx))
    soniq_cmd(
        ctx,
        "dead-letter",
        "replay",
        "--all",
        "--dry-run",
        jobs_env=True,
        expect=[f"Dry run: would replay {count} dead-letter job(s)."],
    )
    soniq_cmd(
        ctx,
        "dead-letter",
        "replay",
        "--all",
        "--yes",
        jobs_env=True,
        expect=[f"Replayed {count} of {count} dead-letter job(s)."],
    )


def test_dead_letter_delete(ctx):
    make_dead_letters(ctx, 3)
    dead = dead_letter_ids(ctx)
    soniq_cmd(ctx, "dead-letter", "delete", dead[0])
    assert dead[0] not in dead_letter_ids(ctx)
    count = len(dead_letter_ids(ctx))
    soniq_cmd(
        ctx,
        "dead-letter",
        "delete",
        "--all",
        "--dry-run",
        expect=[f"Dry run: would delete {count} dead-letter job(s)."],
    )
    soniq_cmd(
        ctx,
        "dead-letter",
        "delete",
        "--all",
        "--yes",
        expect=[f"Deleted {count} dead-letter job(s)."],
    )
    assert dead_letter_ids(ctx) == []


def test_dead_letter_cleanup(ctx):
    soniq_cmd(
        ctx,
        "dead-letter",
        "cleanup",
        "--days",
        "30",
        expect=["dead-letter job(s) older than 30 days."],
    )


# --- long-running commands -------------------------------------------------------


def _run_until(ctx, argv, ready):
    env = cli_env(SONIQ_DATABASE_URL=ctx["url"], SONIQ_JOBS_MODULES="compat_jobs")
    return run_until(argv, env=env, cwd=ctx["dir"], ready=ready)


def test_scheduler(ctx):
    rc, out = _run_until(
        ctx,
        ["scheduler", "--check-interval", "30"],
        lambda out: "Scheduler started" in out,
    )
    assert "Starting Soniq recurring scheduler (checking every 30s)" in out
    assert rc == 0, out


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_dashboard(ctx):
    port = _free_port()
    seen = {}

    def ready(_out):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/stats") as r:
                seen["stats"] = json.loads(r.read())
            return True
        except OSError:
            return False

    _run_until(ctx, ["dashboard", "--host", "127.0.0.1", "--port", str(port)], ready)
    assert isinstance(seen["stats"], dict)
