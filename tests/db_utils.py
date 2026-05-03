import os
import subprocess
from urllib.parse import urlparse, urlunparse

import asyncpg
from asyncpg.pool import Pool

from soniq.backends.postgres.migration_runner import run_migrations

TEST_DB_NAME = "soniq_test"
DEFAULT_TEST_URL = f"postgresql://postgres@localhost/{TEST_DB_NAME}"
TEST_DATABASE_URL = os.environ.get("SONIQ_DATABASE_URL", DEFAULT_TEST_URL)


def make_test_db_url(db_name: str) -> str:
    """Build a database URL for the given DB name, inheriting credentials from CI env."""
    base = os.environ.get("SONIQ_DATABASE_URL", "")
    if base:
        parsed = urlparse(base)
        return urlunparse(parsed._replace(path=f"/{db_name}"))
    return f"postgresql://postgres@localhost/{db_name}"


def _pg_admin_cmd(tool: str, db_name: str, extra_args: list = None) -> tuple:
    """Build a createdb/dropdb command with CI credentials."""
    cmd = [tool] + (extra_args or []) + [db_name]
    env = os.environ.copy()
    base = os.environ.get("SONIQ_DATABASE_URL", "")
    if base:
        parsed = urlparse(base)
        if parsed.password:
            env["PGPASSWORD"] = parsed.password
        if parsed.username:
            cmd = [tool] + ["-U", parsed.username] + (extra_args or []) + [db_name]
        if parsed.hostname:
            cmd.extend(["-h", parsed.hostname])
        if parsed.port:
            cmd.extend(["-p", str(parsed.port)])
    return cmd, env


def run_createdb(db_name: str, check: bool = False):
    """Run createdb with CI-compatible credentials."""
    cmd, env = _pg_admin_cmd("createdb", db_name)
    return subprocess.run(cmd, check=check, env=env, stderr=subprocess.DEVNULL)


def run_dropdb(db_name: str):
    """Run dropdb with CI-compatible credentials."""
    cmd, env = _pg_admin_cmd("dropdb", db_name, extra_args=["--if-exists"])
    return subprocess.run(cmd, check=False, env=env, stderr=subprocess.DEVNULL)


async def create_test_database():
    db_url = os.environ.get(
        "SONIQ_DATABASE_URL", f"postgresql://postgres@localhost/{TEST_DB_NAME}"
    )
    os.environ["SONIQ_DATABASE_URL"] = db_url

    # Only create if it doesn't exist
    try:
        run_createdb(TEST_DB_NAME, check=True)
    except subprocess.CalledProcessError:
        # Database already exists, that's fine
        pass

    # Drop and re-run migrations to ensure clean schema
    temp_pool = await asyncpg.create_pool(db_url)
    async with temp_pool.acquire() as conn:
        # Drop migration tracking so all migrations re-apply cleanly
        await conn.execute("DROP TABLE IF EXISTS soniq_migrations CASCADE")
        # Drop all soniq tables to start fresh
        await conn.execute("DROP TABLE IF EXISTS soniq_task_registry CASCADE")
        await conn.execute("DROP TABLE IF EXISTS soniq_webhook_deliveries CASCADE")
        await conn.execute("DROP TABLE IF EXISTS soniq_webhook_endpoints CASCADE")
        await conn.execute("DROP TABLE IF EXISTS soniq_dead_letter_jobs CASCADE")
        await conn.execute("DROP TABLE IF EXISTS soniq_logs CASCADE")
        await conn.execute("DROP TABLE IF EXISTS soniq_recurring_jobs CASCADE")
        await conn.execute("DROP TABLE IF EXISTS soniq_jobs CASCADE")
        await conn.execute("DROP TABLE IF EXISTS soniq_workers CASCADE")
        await run_migrations(conn)
    await temp_pool.close()


async def drop_test_database():
    # Make dropping optional to avoid issues with concurrent tests
    try:
        run_dropdb(TEST_DB_NAME)
    except Exception:
        pass


async def clear_table(pool: Pool):
    async with pool.acquire() as conn:
        # Clear job tables with CASCADE to handle foreign key constraints.
        # DLQ Option A: soniq_dead_letter_jobs is the authoritative store for
        # dead-lettered jobs and must be truncated alongside soniq_jobs so
        # tests get a clean slate.
        try:
            await conn.execute(
                "TRUNCATE TABLE soniq_jobs, soniq_dead_letter_jobs, soniq_workers "
                "RESTART IDENTITY CASCADE"
            )
        except Exception:
            for stmt in (
                "TRUNCATE TABLE soniq_jobs RESTART IDENTITY",
                "TRUNCATE TABLE soniq_dead_letter_jobs RESTART IDENTITY",
                "TRUNCATE TABLE soniq_workers RESTART IDENTITY CASCADE",
            ):
                try:
                    await conn.execute(stmt)
                except Exception:
                    pass
