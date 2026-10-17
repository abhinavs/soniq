"""Worker.run(shutdown_event=...): caller-owned shutdown, no signal handlers."""

import asyncio
import signal
import time

from soniq.core.registry import JobRegistry
from soniq.testing.memory_backend import MemoryBackend
from tests.unit.test_shutdown_state_machine import (
    _enqueue,
    _make_worker,
    _wait_for_claim,
)


async def test_event_drains_in_flight_job_and_leaves_signals_alone():
    backend = MemoryBackend()
    await backend.initialize()
    registry = JobRegistry()
    done = asyncio.Event()

    async def slow_job():
        await asyncio.sleep(0.4)
        done.set()

    job_id = await _enqueue(backend, registry, slow_job)
    worker, executor = await _make_worker(registry, backend, shutdown_timeout=10.0)
    before = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    event = asyncio.Event()
    try:
        run_task = asyncio.create_task(worker.run(concurrency=1, shutdown_event=event))
        await _wait_for_claim(backend, job_id)
        for s, handler in before.items():
            assert signal.getsignal(s) is handler
        event.set()
        await asyncio.wait_for(run_task, timeout=10.0)
        assert done.is_set()
        assert (await backend.get_job(job_id))["status"] == "done"
    finally:
        executor.shutdown(wait=True)


async def test_event_force_timeout_requeues_job():
    backend = MemoryBackend()
    await backend.initialize()
    registry = JobRegistry()

    async def stuck_job():
        await asyncio.sleep(60)

    job_id = await _enqueue(backend, registry, stuck_job)
    worker, executor = await _make_worker(registry, backend, shutdown_timeout=0.3)
    event = asyncio.Event()
    try:
        run_task = asyncio.create_task(worker.run(concurrency=1, shutdown_event=event))
        await _wait_for_claim(backend, job_id)
        event.set()
        t0 = time.monotonic()
        await asyncio.wait_for(run_task, timeout=10.0)
        assert time.monotonic() - t0 < 5.0
        assert (await backend.get_job(job_id))["status"] == "queued"
    finally:
        executor.shutdown(wait=True)
