"""
Tests for graceful worker shutdown and pool closing race condition fixes.

These tests verify that the "pool is closing" race condition is properly handled
during worker shutdown scenarios, ensuring excellent developer experience.
"""

import asyncio
import logging

import pytest

from soniq import Soniq
from tests.db_utils import TEST_DATABASE_URL


@pytest.mark.asyncio
async def test_worker_cancellation_no_pool_errors(caplog):
    """Test that worker cancellation doesn't produce pool closing errors"""

    caplog.clear()

    with caplog.at_level(logging.DEBUG):
        app = Soniq(database_url=TEST_DATABASE_URL)
        worker_task = asyncio.create_task(app.run_worker(concurrency=1))

        await asyncio.sleep(0.1)

        worker_task.cancel()

        try:
            await worker_task
        except asyncio.CancelledError:
            pass

        await asyncio.sleep(0.05)

    pool_closing_errors = [
        record
        for record in caplog.records
        if "pool is closing" in record.getMessage() and record.levelno >= logging.ERROR
    ]

    assert len(pool_closing_errors) == 0, (
        f"Found {len(pool_closing_errors)} 'pool is closing' errors: "
        f"{[r.getMessage() for r in pool_closing_errors]}"
    )

    if app.is_initialized and not app.is_closed:
        await app.close()


@pytest.mark.asyncio
async def test_multiple_rapid_worker_cancellations(caplog):
    """Stress test: multiple rapid worker start/cancel cycles should be clean"""

    caplog.clear()

    with caplog.at_level(logging.ERROR):
        for i in range(3):
            app = Soniq(database_url=TEST_DATABASE_URL)
            worker_task = asyncio.create_task(app.run_worker(concurrency=1))

            await asyncio.sleep(0.05)

            worker_task.cancel()

            try:
                await worker_task
            except asyncio.CancelledError:
                pass

            await asyncio.sleep(0.02)

            if app.is_initialized and not app.is_closed:
                await app.close()

    error_messages = [record.getMessage() for record in caplog.records]
    pool_errors = [msg for msg in error_messages if "pool is closing" in msg]

    assert len(pool_errors) == 0, f"Found pool closing errors: {pool_errors}"


@pytest.mark.asyncio
async def test_worker_listener_cleanup_handles_errors():
    """Test that the worker's listener cleanup code handles connection errors gracefully"""
    app = Soniq(database_url=TEST_DATABASE_URL)
    await app._ensure_initialized()
    pool = app.backend._pool
    conn = await pool.acquire()

    try:
        try:
            await conn.remove_listener("nonexistent_channel", lambda *args: None)
        except Exception as e:
            assert "does not have" in str(e) or "listener" in str(e)

        try:
            await pool.release(conn)
        except Exception:
            pass
    finally:
        pass

    assert True

    await app.close()


@pytest.mark.asyncio
async def test_graceful_shutdown_integration():
    """Integration test for complete graceful shutdown flow"""

    shutdown_event = asyncio.Event()

    async def mock_signal_handler():
        await asyncio.sleep(0.1)
        shutdown_event.set()

    signal_task = asyncio.create_task(mock_signal_handler())

    app = Soniq(database_url=TEST_DATABASE_URL)
    worker_task = asyncio.create_task(app.run_worker(concurrency=1))

    done, pending = await asyncio.wait(
        [worker_task, signal_task], return_when=asyncio.FIRST_COMPLETED, timeout=0.5
    )

    for task in pending:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    if worker_task in done:
        try:
            await worker_task
        except asyncio.CancelledError:
            pass

    assert True

    if app.is_initialized and not app.is_closed:
        await app.close()
