"""A retry due before the next poll runs on time, not after poll_interval."""

import asyncio
import time

from soniq import Soniq


async def test_short_retry_does_not_wait_for_the_poll():
    app = Soniq(backend="memory", poll_interval=10.0)
    calls: list[float] = []
    done = asyncio.Event()

    @app.job(name="flaky", retries=1, retry_delay=0.3, retry_jitter=False)
    async def flaky():
        calls.append(time.monotonic())
        if len(calls) == 1:
            raise RuntimeError("first try fails")
        done.set()

    await flaky.enqueue()
    stop = asyncio.Event()
    worker = asyncio.create_task(app.run_worker(concurrency=1, shutdown_event=stop))
    try:
        await asyncio.wait_for(done.wait(), timeout=3)
    finally:
        stop.set()
        await worker
    assert calls[1] - calls[0] < 3
