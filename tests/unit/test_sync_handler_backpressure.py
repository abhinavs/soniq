"""
Sync handler offload + post-claim backpressure.

These tests pin the two invariants from the contract:

1. Active sync threads at any instant <= ``sync_handler_pool_size``.
2. Claimed ``processing`` rows at any instant <= worker concurrency.

Both invariants are enforced by the per-instance bounded
``ThreadPoolExecutor`` plus the post-claim ``asyncio.Semaphore`` wired up
through ``Soniq._get_sync_dispatch`` and threaded into
``process_job_via_backend``.

The semaphore release is tied to the executor future's done-callback
(via ``loop.call_soon_threadsafe``) - not to the ``await`` path - so
``asyncio.wait_for`` timeouts can never let a fresh job acquire a slot
while the original thread is still running.
"""

import asyncio
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from soniq.core.processor import process_job_via_backend
from soniq.core.registry import JobRegistry
from soniq.testing.memory_backend import MemoryBackend


async def _create_jobs(backend, registry, job_func, count, args_template=None):
    job_name = job_func.__name__
    if registry.get_job(job_name) is None:
        registry.register_job(job_func, name=job_func.__name__)
    for i in range(count):
        await backend.create_job(
            job_id=str(uuid.uuid4()),
            job_name=job_name,
            args=args_template or {"n": i},
            args_hash=None,
            max_attempts=3,
            priority=100,
            queue="default",
            unique=False,
            dedup_key=None,
            scheduled_at=None,
        )


@pytest.mark.asyncio
async def test_sync_pool_size_is_respected_under_oversubscription():
    """Two-invariants test.

    Concurrency = 4, pool_size = 2, total jobs = 8 (= 2 * pool_size * 2).
    All handlers are sync and block on a barrier-like pattern long enough
    that we can sample the active count repeatedly.

    The processor's post-claim semaphore must keep concurrent live sync
    threads at <= pool_size at all times. Worker concurrency 4 still
    holds because the semaphore stalls excess workers in
    ``processing`` state until a slot frees, but at no instant should
    more than ``pool_size`` threads be inside the handler.
    """
    backend = MemoryBackend()
    await backend.initialize()
    registry = JobRegistry()

    pool_size = 2
    concurrency = 4
    total_jobs = 8

    active = 0
    peak = 0
    counter_lock = threading.Lock()
    samples: list[int] = []

    def sync_handler(n: int):
        nonlocal active, peak
        with counter_lock:
            active += 1
            peak = max(peak, active)
            samples.append(active)
        # Stay in the executor long enough that oversubscription would
        # be visible if it were possible.
        time.sleep(0.05)
        with counter_lock:
            active -= 1

    await _create_jobs(backend, registry, sync_handler, count=total_jobs)

    executor = ThreadPoolExecutor(max_workers=pool_size, thread_name_prefix="test-sync")
    semaphore = asyncio.Semaphore(pool_size)

    in_flight_slots = [{} for _ in range(concurrency)]

    # Invariant (b) sampler: at every tick assert that the count of
    # backend rows currently in 'processing' never exceeds worker
    # concurrency. The worker loop owns this invariant; we co-run a
    # sampler task to make a violation visible if it ever happens.
    sampler_stop = asyncio.Event()
    processing_peak = 0

    async def processing_sampler():
        nonlocal processing_peak
        while not sampler_stop.is_set():
            count = sum(
                1 for j in backend._jobs.values() if j.get("status") == "processing"
            )
            processing_peak = max(processing_peak, count)
            assert count <= concurrency, (
                f"invariant (b) violated: processing rows={count} > "
                f"worker_concurrency={concurrency}"
            )
            await asyncio.sleep(0.005)

    async def worker_task(slot):
        while True:
            processed = await process_job_via_backend(
                backend=backend,
                job_registry=registry,
                queues=["default"],
                sync_executor=executor,
                sync_pool_semaphore=semaphore,
                in_flight_slot=slot,
            )
            if not processed:
                return

    sampler_task = asyncio.create_task(processing_sampler())
    try:
        await asyncio.gather(*(worker_task(s) for s in in_flight_slots))
    finally:
        sampler_stop.set()
        await sampler_task
        executor.shutdown(wait=True)

    # Invariant (a): concurrent sync threads never exceeded the executor cap.
    assert peak <= pool_size, (
        f"sync executor cap violated: peak={peak} > pool_size={pool_size}; "
        f"samples (head)={samples[:20]}"
    )
    # Invariant (b) sanity: we did observe the workers claiming rows
    # (otherwise the sampler proved nothing).
    assert processing_peak >= 1, (
        "sampler saw no 'processing' rows; test would not have detected "
        "an invariant (b) violation"
    )
    # All jobs ran.
    assert len(samples) == total_jobs


@pytest.mark.asyncio
async def test_sync_handler_pool_semaphore_does_not_leak_on_exception():
    """If a sync handler raises, the semaphore permit is still released
    (via the executor future's done-callback). Otherwise the pool would
    permanently lose a slot per failure.
    """
    backend = MemoryBackend()
    await backend.initialize()
    registry = JobRegistry()

    pool_size = 1

    def boom(n: int):
        raise RuntimeError("intentional")

    await _create_jobs(backend, registry, boom, count=3)

    executor = ThreadPoolExecutor(max_workers=pool_size, thread_name_prefix="test-sync")
    semaphore = asyncio.Semaphore(pool_size)

    try:
        for _ in range(3):
            slot = {}
            await process_job_via_backend(
                backend=backend,
                job_registry=registry,
                queues=["default"],
                sync_executor=executor,
                sync_pool_semaphore=semaphore,
                in_flight_slot=slot,
            )
    finally:
        executor.shutdown(wait=True)

    # All 3 jobs were attempted; semaphore is back at full.
    # We can verify by asserting we can acquire `pool_size` permits without
    # blocking.
    for _ in range(pool_size):
        # acquire should not block; wrap in wait_for to fail fast.
        await asyncio.wait_for(semaphore.acquire(), timeout=0.5)


@pytest.mark.asyncio
async def test_async_handlers_bypass_sync_pool():
    """Async handlers must not consume sync executor slots or semaphore
    permits, otherwise an async-only deployment with ``pool_size=1``
    would serialize itself.
    """
    backend = MemoryBackend()
    await backend.initialize()
    registry = JobRegistry()

    concurrent = 0
    peak_concurrent = 0
    cond_lock = asyncio.Lock()

    async def slow_async(n: int):
        nonlocal concurrent, peak_concurrent
        async with cond_lock:
            concurrent += 1
            peak_concurrent = max(peak_concurrent, concurrent)
        await asyncio.sleep(0.05)
        async with cond_lock:
            concurrent -= 1

    await _create_jobs(backend, registry, slow_async, count=4)

    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-sync")
    semaphore = asyncio.Semaphore(1)  # would serialize sync work

    try:

        async def worker_task(slot):
            while True:
                processed = await process_job_via_backend(
                    backend=backend,
                    job_registry=registry,
                    queues=["default"],
                    sync_executor=executor,
                    sync_pool_semaphore=semaphore,
                    in_flight_slot=slot,
                )
                if not processed:
                    return

        await asyncio.gather(*(worker_task({}) for _ in range(4)))
    finally:
        executor.shutdown(wait=True)

    # Async handlers ran in parallel even though the sync pool is size 1.
    assert peak_concurrent > 1, (
        "async handlers were serialized through the sync pool; "
        f"peak_concurrent={peak_concurrent}"
    )
