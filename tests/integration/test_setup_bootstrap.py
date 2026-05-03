"""
Bootstrap test: Soniq.setup() must create a missing Postgres database.

Contract: ``Soniq.setup()`` documents that it creates the database if
missing before running migrations. The pool init must therefore wait
until the database is known to exist; otherwise first-run on a missing
DB blows up at the connection step.

This pins the documented behavior end-to-end against a fresh database
that does not yet exist.
"""

from __future__ import annotations

import os
import subprocess
from urllib.parse import urlparse, urlunparse

import asyncpg
import pytest

from soniq import Soniq
from tests.db_utils import TEST_DATABASE_URL

BOOTSTRAP_DB = "soniq_bootstrap_test"


def _make_url(db_name: str) -> str:
    parsed = urlparse(TEST_DATABASE_URL)
    return urlunparse(parsed._replace(path=f"/{db_name}"))


def _drop_db(name: str) -> None:
    parsed = urlparse(TEST_DATABASE_URL)
    env = os.environ.copy()
    if parsed.password:
        env["PGPASSWORD"] = parsed.password
    args = ["dropdb", "--if-exists"]
    if parsed.username:
        args += ["-U", parsed.username]
    if parsed.hostname:
        args += ["-h", parsed.hostname]
    if parsed.port:
        args += ["-p", str(parsed.port)]
    args += [name]
    subprocess.run(args, env=env, check=False, stderr=subprocess.DEVNULL)


async def _db_exists(db_name: str) -> bool:
    parsed = urlparse(TEST_DATABASE_URL)
    admin_url = urlunparse(parsed._replace(path="/postgres"))
    conn = await asyncpg.connect(admin_url)
    try:
        row = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", db_name
        )
        return row is not None
    finally:
        await conn.close()


@pytest.fixture
async def missing_db():
    _drop_db(BOOTSTRAP_DB)
    assert not await _db_exists(
        BOOTSTRAP_DB
    ), "Pre-condition failed: bootstrap test DB still exists after dropdb."
    yield BOOTSTRAP_DB
    _drop_db(BOOTSTRAP_DB)


@pytest.mark.asyncio
async def test_setup_creates_missing_database(missing_db):
    """A fresh Soniq pointing at a non-existent DB must succeed at setup()."""
    url = _make_url(missing_db)
    app = Soniq(database_url=url)
    try:
        await app.setup()
    finally:
        await app.close()

    assert await _db_exists(
        missing_db
    ), "setup() must create the database when it doesn't exist"


@pytest.mark.asyncio
async def test_setup_is_idempotent(missing_db):
    """Running setup() twice on the same fresh DB is a clean no-op the second time."""
    url = _make_url(missing_db)

    app1 = Soniq(database_url=url)
    try:
        await app1.setup()
    finally:
        await app1.close()

    app2 = Soniq(database_url=url)
    try:
        await app2.setup()
    finally:
        await app2.close()

    assert await _db_exists(missing_db)
