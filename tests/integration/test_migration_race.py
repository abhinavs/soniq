"""
Parallel deploys can both call `soniq setup` at once. The migration
runner has to serialize or the losing node errors on a non-idempotent DDL
or double-inserts into `soniq_migrations`. We guard that with a
session-scoped `pg_advisory_lock` inside `MigrationRunner`.

This test simulates the race: two concurrent runners against a freshly
dropped schema must both succeed, and the migrations table must contain
each version exactly once.
"""

import asyncio
import os
import subprocess
from urllib.parse import urlparse, urlunparse

import asyncpg
import pytest

from soniq.backends.postgres.migration_runner import MigrationRunner
from tests.db_utils import TEST_DATABASE_URL

DB_NAME = "soniq_pr5_migration_race"


def _db_url(name: str) -> str:
    parsed = urlparse(TEST_DATABASE_URL)
    return urlunparse(parsed._replace(path=f"/{name}"))


async def _reset_db():
    parsed = urlparse(TEST_DATABASE_URL)
    env = os.environ.copy()
    if parsed.password:
        env["PGPASSWORD"] = parsed.password
    base_args = []
    if parsed.username:
        base_args += ["-U", parsed.username]
    if parsed.hostname:
        base_args += ["-h", parsed.hostname]
    if parsed.port:
        base_args += ["-p", str(parsed.port)]
    subprocess.run(
        ["dropdb", "--if-exists", *base_args, DB_NAME],
        env=env,
        check=False,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        ["createdb", *base_args, DB_NAME],
        env=env,
        check=False,
        stderr=subprocess.DEVNULL,
    )


@pytest.mark.asyncio
async def test_parallel_migrations_no_duplicate_apply():
    await _reset_db()
    url = _db_url(DB_NAME)

    runner = MigrationRunner()

    async def run_once():
        async with asyncpg.create_pool(url, min_size=1, max_size=2) as pool:
            async with pool.acquire() as conn:
                return await runner._run_migrations_with_connection(conn)

    # Two concurrent migration runs on an empty database.
    applied_a, applied_b = await asyncio.gather(run_once(), run_once())

    # Combined applied count equals the number of available migrations;
    # each migration was applied exactly once even under concurrency.
    expected = len(runner.discover_migrations())
    assert applied_a + applied_b == expected

    # The migrations table has exactly one row per version.
    async with asyncpg.create_pool(url) as pool:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT version, COUNT(*) AS n "
                "FROM soniq_migrations GROUP BY version"
            )
    counts = {row["version"]: row["n"] for row in rows}
    assert len(counts) == expected
    assert all(n == 1 for n in counts.values()), counts
