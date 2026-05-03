"""
The `soniq.testing` package is the public test-tier API. These tests
pin its surface so accidental breakage shows up in unit runs.
"""

import asyncio

import pytest

from soniq.testing import MemoryBackend, make_app, wait_until


def test_public_surface():
    # Each helper imports from the package level; users never need to
    # know the internal module layout.
    assert MemoryBackend is not None
    assert callable(make_app)
    assert asyncio.iscoroutinefunction(wait_until)


def test_make_app_uses_memory_backend():
    app = make_app()
    # Internals: assert the resolved backend is the in-memory one.
    # No real DB call needed; the backend is constructed eagerly.
    from soniq.testing.memory_backend import MemoryBackend as MB

    assert isinstance(app._backend, MB)


@pytest.mark.asyncio
async def test_wait_until_succeeds_when_predicate_already_true():
    await wait_until(lambda: True, timeout=1.0)


@pytest.mark.asyncio
async def test_wait_until_polls_until_predicate_flips():
    state = {"ready": False}

    async def flip():
        await asyncio.sleep(0.05)
        state["ready"] = True

    asyncio.create_task(flip())
    await wait_until(lambda: state["ready"], timeout=1.0, poll=0.01)
    assert state["ready"] is True


@pytest.mark.asyncio
async def test_wait_until_raises_on_timeout():
    with pytest.raises(AssertionError, match="custom timeout message"):
        await wait_until(
            lambda: False,
            timeout=0.05,
            poll=0.01,
            message="custom timeout message",
        )


@pytest.mark.asyncio
async def test_wait_until_accepts_async_predicate():
    state = {"calls": 0}

    async def predicate():
        state["calls"] += 1
        return state["calls"] >= 3

    await wait_until(predicate, timeout=1.0, poll=0.01)
    assert state["calls"] >= 3


@pytest.mark.asyncio
async def test_make_app_runs_a_job_end_to_end():
    """A round-trip through the in-memory backend using the public API."""
    app = make_app()
    executed = []

    @app.job(name="collect")
    async def collect(value: str):
        executed.append(value)

    await app.enqueue("collect", args={"value": "hi"})
    await app.run_worker(run_once=True)

    assert executed == ["hi"]
    await app.close()
