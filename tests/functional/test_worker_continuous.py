"""
Test Worker continuous mode with MemoryBackend.
"""

import asyncio

from soniq import Soniq


async def test_worker_processes_job_and_shuts_down():
    """Start continuous worker, enqueue a job, verify it runs, shut down."""
    app = Soniq(backend="memory")
    await app._ensure_initialized()

    executed = asyncio.Event()

    @app.job(name="signal_done")
    async def signal_done():
        executed.set()

    await app.enqueue("signal_done")

    async def run_and_stop():
        from soniq.core.worker import Worker

        worker = Worker(
            backend=app._backend,
            registry=app._get_job_registry(),
            settings=app.settings,
        )
        # Override poll_interval to poll quickly
        app.settings.poll_interval = 0.05

        # Run worker in background
        worker_task = asyncio.create_task(
            worker._run_continuous(concurrency=1, queues=None)
        )

        # Wait for job to execute
        await asyncio.wait_for(executed.wait(), timeout=3.0)

        # Trigger shutdown
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass

    await run_and_stop()
    assert executed.is_set()
    await app.close()


async def test_worker_run_method_dispatches_correctly():
    """Worker.run() with run_once=True processes jobs and exits."""
    app = Soniq(backend="memory")
    await app._ensure_initialized()

    results = []

    @app.job(name="collect")
    async def collect(value: str):
        results.append(value)

    await app.enqueue("collect", args={"value": "a"})
    await app.enqueue("collect", args={"value": "b"})

    from soniq.core.worker import Worker

    worker = Worker(
        backend=app._backend,
        registry=app._get_job_registry(),
        settings=app.settings,
    )
    processed = await worker.run(run_once=True)
    assert processed is True
    assert sorted(results) == ["a", "b"]

    await app.close()
