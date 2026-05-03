"""Test async context manager on Soniq."""

from soniq import Soniq


async def test_async_context_manager():
    """Soniq should work as an async context manager."""
    async with Soniq(backend="memory") as app:
        assert app.is_initialized is True
        assert app.is_closed is False
    assert app.is_closed is True


async def test_context_manager_enqueue_and_process():
    """Full round-trip inside async with block."""
    executed = []

    async with Soniq(backend="memory") as app:

        @app.job(name="greet")
        async def greet(name: str):
            executed.append(name)

        await app.enqueue("greet", args={"name": "world"})
        await app.run_worker(run_once=True)

    assert executed == ["world"]
    assert app.is_closed
