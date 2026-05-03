"""
Tests for connection pool exhaustion behavior.
"""

import asyncio

import pytest

from soniq.app import Soniq
from tests.db_utils import TEST_DATABASE_URL


@pytest.mark.asyncio
async def test_pool_exhaustion_blocks_then_succeeds():
    """
    When all pool connections are in use, operations should wait for a
    connection to become available rather than crashing.
    """
    # Create an instance with a very small pool
    app = Soniq(
        database_url=TEST_DATABASE_URL,
        pool_min_size=1,
        pool_max_size=2,
    )
    await app._ensure_initialized()

    try:
        pool = await app._get_pool()

        # Hold both connections
        conn1 = await pool.acquire()
        conn2 = await pool.acquire()

        # Third acquire should block/timeout
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(pool.acquire(), timeout=0.5)

        # Release one connection
        await pool.release(conn2)

        # Now acquire should succeed
        conn3 = await asyncio.wait_for(pool.acquire(), timeout=2.0)
        assert conn3 is not None
        await pool.release(conn3)
        await pool.release(conn1)
    finally:
        await app.close()


@pytest.mark.asyncio
async def test_pool_size_check_raises_on_undersized_pool():
    """
    _check_pool_sizing refuses to start when concurrency + headroom exceeds
    pool_max_size. The old behavior was a warning that operators missed;
    we now fail fast so a misconfigured deploy does not deadlock under load.
    """
    from soniq.errors import SoniqError

    app = Soniq(
        database_url=TEST_DATABASE_URL,
        pool_min_size=1,
        pool_max_size=3,
        pool_headroom=2,
    )
    await app._ensure_initialized()

    try:
        with pytest.raises(SoniqError, match="SONIQ_POOL_TOO_SMALL"):
            # concurrency=5 + headroom=2 = 7 > max_size=3
            app._check_pool_sizing(concurrency=5)
    finally:
        await app.close()
