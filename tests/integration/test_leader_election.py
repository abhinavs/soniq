"""
Integration test for Postgres advisory-lock leader election.

Two concurrent `with_advisory_lock(name)` calls on the same backend must
see exactly one leader at a time. When the leader exits the context, a
subsequent caller can become the new leader.
"""

import asyncio

import pytest

from soniq.backends.postgres import PostgresBackend
from soniq.core.leadership import with_advisory_lock
from tests.db_utils import TEST_DATABASE_URL


@pytest.fixture
async def backend():
    b = PostgresBackend(database_url=TEST_DATABASE_URL)
    await b.initialize()
    yield b
    await b.close()


@pytest.mark.asyncio
async def test_only_one_leader_at_a_time(backend):
    """Two concurrent holders on the same key: exactly one becomes leader."""

    ready = asyncio.Event()
    release = asyncio.Event()
    results: list[bool] = []

    async def holder():
        async with with_advisory_lock(backend, "test.leader.exclusive") as leader:
            results.append(leader)
            if leader:
                ready.set()
                await release.wait()

    tasks = [asyncio.create_task(holder()) for _ in range(2)]
    await ready.wait()

    # Second caller should have already fallen through with leader=False
    # while the first still holds the lock.
    await asyncio.sleep(0.05)
    assert results.count(True) == 1
    assert results.count(False) == 1

    release.set()
    await asyncio.gather(*tasks)


@pytest.mark.asyncio
async def test_lock_releases_after_exit(backend):
    """After the leader exits, a new caller can acquire."""

    async with with_advisory_lock(backend, "test.leader.sequence") as leader:
        assert leader is True

    async with with_advisory_lock(backend, "test.leader.sequence") as leader:
        assert leader is True


@pytest.mark.asyncio
async def test_distinct_keys_do_not_interfere(backend):
    """Two different keys can be held simultaneously."""

    async with with_advisory_lock(backend, "test.leader.a") as leader_a:
        async with with_advisory_lock(backend, "test.leader.b") as leader_b:
            assert leader_a is True
            assert leader_b is True
