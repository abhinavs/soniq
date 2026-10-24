"""Shared helpers for the compatibility suite.

These tests guard the public surface that existing users depend on. They
need a real PostgreSQL server. Each test that needs one asks the
``fresh_db`` fixture for a database; the fixture drops and recreates a
database named ``soniq_compat*`` on the server from ``SONIQ_DATABASE_URL``
(the database name in that URL is never touched).

When no server is reachable the tests fail. Set ``SONIQ_COMPAT_SKIP_DB=1``
to skip them instead, for example on a laptop without Postgres.
"""

import asyncio
import os
import signal
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse, urlunparse

import asyncpg
import pytest

SKIP_ENV = "SONIQ_COMPAT_SKIP_DB"


def db_url(name: str) -> str:
    base = os.environ.get(
        "SONIQ_DATABASE_URL", "postgresql://postgres@localhost:5432/postgres"
    )
    return urlunparse(urlparse(base)._replace(path=f"/{name}"))


async def _recreate(name: str) -> None:
    conn = await asyncpg.connect(db_url("postgres"))
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{name}"')
    finally:
        await conn.close()


def recreate_db(name: str = "soniq_compat") -> str:
    """Drop and create database ``name``. Return its URL."""
    assert name.startswith("soniq_compat"), name
    try:
        # A worker thread, so this also works from inside async tests.
        with ThreadPoolExecutor(1) as pool:
            pool.submit(asyncio.run, _recreate(name)).result()
    except (OSError, asyncpg.PostgresError) as exc:
        if os.environ.get(SKIP_ENV):
            pytest.skip(f"PostgreSQL not reachable and {SKIP_ENV} is set")
        pytest.fail(
            f"PostgreSQL is required for the compatibility suite ({exc}). "
            f"Set SONIQ_DATABASE_URL, or set {SKIP_ENV}=1 to skip."
        )
    return db_url(name)


@pytest.fixture
def fresh_db():
    """``fresh_db("soniq_compat_x")`` -> URL of an empty database."""
    return recreate_db


def cli_env(**extra: str) -> dict:
    """``os.environ`` without Soniq settings, plus ``extra``."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("SONIQ_")}
    env.update(extra)
    return env


def run_cli(*argv: str, env: dict, cwd, timeout: float = 60):
    """Run the current code's ``soniq`` CLI in a subprocess."""
    return subprocess.run(
        [sys.executable, "-m", "soniq.cli.main", *argv],
        env=env,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def run_until(argv, *, env: dict, cwd, ready, timeout: float = 30):
    """Start ``soniq *argv``, wait until ``ready(output)`` is true, then press
    Ctrl+C. Return ``(exit code, output)``."""
    with tempfile.TemporaryFile("w+") as log:
        proc = subprocess.Popen(
            [sys.executable, "-m", "soniq.cli.main", *argv],
            env=env,
            cwd=cwd,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
        )

        def output():
            log.seek(0)
            return log.read()

        try:
            deadline = time.monotonic() + timeout
            while not ready(output()):
                assert proc.poll() is None, f"exited early:\n{output()}"
                assert time.monotonic() < deadline, f"not ready:\n{output()}"
                time.sleep(0.1)
        finally:
            proc.send_signal(signal.SIGINT)
            rc = proc.wait(timeout=30)
        return rc, output()


def old_venv(cache_dir, version: str):
    """A virtualenv in ``cache_dir`` with soniq ``version`` from PyPI."""
    python = cache_dir / "bin" / "python"
    probe = [str(python), "-c", "import soniq; print(soniq.__version__)"]
    if python.exists():
        found = subprocess.run(probe, capture_output=True, text=True, cwd=cache_dir)
        if found.stdout.strip() == version:
            return cache_dir
    subprocess.run(
        [sys.executable, "-m", "venv", "--clear", str(cache_dir)], check=True
    )
    subprocess.run(
        [str(python), "-m", "pip", "install", "-q", f"soniq=={version}"],
        check=True,
        timeout=300,
    )
    return cache_dir


def fetch(url, query):
    """Rows for ``query``, from a fresh connection."""

    async def go():
        conn = await asyncpg.connect(url)
        try:
            return await conn.fetch(query)
        finally:
            await conn.close()

    return asyncio.run(go())


def check(proc, *expect):
    """Assert ``proc`` succeeded and printed every line in ``expect``."""
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    for line in expect:
        assert line in out, f"{line!r} missing:\n{out}"
    return out
