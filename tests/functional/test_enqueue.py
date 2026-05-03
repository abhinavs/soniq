"""
Test that Soniq.enqueue() works with MemoryBackend.

This verifies that enqueue goes through the backend abstraction,
not through raw asyncpg SQL.
"""

import pytest

from soniq import Soniq


@pytest.fixture
async def app():
    app = Soniq(backend="memory")
    await app._ensure_initialized()
    yield app
    await app.close()


async def test_enqueue_with_memory_backend(app):
    """Enqueue a job using MemoryBackend — should not crash."""
    executed = []

    @app.job(name="greet")
    async def greet(name: str):
        executed.append(name)

    job_id = await app.enqueue("greet", args={"name": "world"})
    assert isinstance(job_id, str)
    assert len(job_id) > 0


async def test_enqueue_and_process_round_trip(app):
    """Enqueue, process with run_worker(run_once=True), verify execution."""
    executed = []

    @app.job(name="greet")
    async def greet(name: str):
        executed.append(name)

    job_id = await app.enqueue("greet", args={"name": "world"})
    assert isinstance(job_id, str)

    await app.run_worker(run_once=True)
    assert executed == ["world"]
