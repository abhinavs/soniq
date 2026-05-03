"""
Shutdown state machine.

Source of truth: ``docs/_internals/contracts/shutdown.md``. The state machine is:

    RUNNING -> DRAINING (on SIGTERM)
    DRAINING -> STOPPED (clean drain within shutdown_timeout)
    DRAINING -> FORCE_TIMEOUT_PATH (shutdown_timeout expired)
    FORCE_TIMEOUT_PATH (async branch) -> STOPPED (cancel + nack_job)
    FORCE_TIMEOUT_PATH (sync branch) -> STOPPED (after thread returns;
        sync_handler_grace_seconds is a flat budget from the
        FORCE_TIMEOUT_PATH instant, then WAIT_FOR_THREAD unbounded)

Sync handlers are never `nack_job`'d: their thread is uncancelable and
the worker_task waits via ``asyncio.wrap_future`` until it returns.

Each test below pins one transition end-to-end against ``Worker.run``
with ``MemoryBackend`` (no Postgres dependency). The shared
``capture_shutdown_event`` fixture monkey-patches
``GracefulSignalHandler.setup_signal_handlers`` so the test can flip the
event directly instead of relying on real OS signals (SIGTERM in pytest
is flaky and can collide with the test runner).
"""

import asyncio
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from soniq.core.registry import JobRegistry
from soniq.core.worker import Worker
from soniq.settings import SoniqSettings
from soniq.testing.memory_backend import MemoryBackend


@pytest.fixture
def capture_shutdown_event(monkeypatch):
    """Patch GracefulSignalHandler so the test can grab the event.

    Returns a holder dict; ``holder["event"]`` is populated as soon as the
    Worker calls ``setup_signal_handlers``.
    """
    from soniq.utils import signals as signals_mod

    holder: dict = {}
    original = signals_mod.GracefulSignalHandler.setup_signal_handlers

    def patched(self, shutdown_event):
        holder["event"] = shutdown_event
        # Skip real signal registration; directly remember the event.
        self.shutdown_event = shutdown_event

    monkeypatch.setattr(
        signals_mod.GracefulSignalHandler, "setup_signal_handlers", patched
    )
    yield holder
    signals_mod.GracefulSignalHandler.setup_signal_handlers = original


async def _make_worker(
    registry,
    backend,
    *,
    shutdown_timeout=30.0,
    sync_handler_grace_seconds=None,
    job_timeout=None,
    sync_pool_size=4,
):
    settings = SoniqSettings(
        database_url="postgresql://placeholder/_unused",
        shutdown_timeout=shutdown_timeout,
        sync_handler_grace_seconds=sync_handler_grace_seconds,
        job_timeout=job_timeout if job_timeout is not None else 0,
        sync_handler_pool_size=sync_pool_size,
        poll_interval=0.1,
        heartbeat_interval=0.5,
        cleanup_interval=10.0,
    )
    executor = ThreadPoolExecutor(max_workers=sync_pool_size, thread_name_prefix="t")
    semaphore = asyncio.Semaphore(sync_pool_size)
    worker = Worker(
        backend=backend,
        registry=registry,
        settings=settings,
        sync_executor=executor,
        sync_pool_semaphore=semaphore,
    )
    return worker, executor


async def _enqueue(backend, registry, func, args=None, queue="default"):
    job_name = func.__name__
    if registry.get_job(job_name) is None:
        registry.register_job(func, name=job_name)
    job_id = str(uuid.uuid4())
    await backend.create_job(
        job_id=job_id,
        job_name=job_name,
        args=args or {},
        args_hash=None,
        max_attempts=3,
        priority=100,
        queue=queue,
        unique=False,
        dedup_key=None,
        scheduled_at=None,
    )
    return job_id


async def _wait_for_claim(backend, job_id, timeout=2.0):
    """Spin until a specific job is claimed (status='processing')."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = await backend.get_job(job_id)
        if job and job.get("status") == "processing":
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"job {job_id} was never claimed within {timeout}s")


@pytest.mark.asyncio
async def test_async_drain_completes_within_shutdown_timeout(capture_shutdown_event):
    """Async handler that finishes inside shutdown_timeout: clean drain."""
    backend = MemoryBackend()
    await backend.initialize()
    registry = JobRegistry()

    completed = asyncio.Event()

    async def slow_async():
        await asyncio.sleep(0.5)
        completed.set()

    job_id = await _enqueue(backend, registry, slow_async)

    worker, executor = await _make_worker(registry, backend, shutdown_timeout=10.0)
    try:
        run_task = asyncio.create_task(worker.run(concurrency=1))
        await _wait_for_claim(backend, job_id)

        capture_shutdown_event["event"].set()

        t0 = time.monotonic()
        await asyncio.wait_for(run_task, timeout=10.0)
        elapsed = time.monotonic() - t0

        # Drain finished well inside shutdown_timeout (10s).
        assert elapsed < 5.0, f"drain took {elapsed:.2f}s; expected <5s"
        assert completed.is_set()

        job = await backend.get_job(job_id)
        assert job["status"] == "done"
    finally:
        executor.shutdown(wait=True)


@pytest.mark.asyncio
async def test_async_force_timeout_nacks_job(capture_shutdown_event):
    """Async handler longer than shutdown_timeout: cancel + nack_job."""
    backend = MemoryBackend()
    await backend.initialize()
    registry = JobRegistry()

    handler_started = asyncio.Event()

    async def very_slow_async():
        handler_started.set()
        await asyncio.sleep(60.0)

    job_id = await _enqueue(backend, registry, very_slow_async)

    worker, executor = await _make_worker(registry, backend, shutdown_timeout=1.0)
    try:
        run_task = asyncio.create_task(worker.run(concurrency=1))
        await asyncio.wait_for(handler_started.wait(), timeout=2.0)

        capture_shutdown_event["event"].set()

        t0 = time.monotonic()
        await asyncio.wait_for(run_task, timeout=10.0)
        elapsed = time.monotonic() - t0

        # FORCE_TIMEOUT_PATH fired around 1.0s; cancellation + nack_job
        # is bounded - whole run.run should finish well under 5s.
        assert elapsed < 5.0, f"force-timeout took {elapsed:.2f}s"

        job = await backend.get_job(job_id)
        assert (
            job["status"] == "queued"
        ), f"async job should be NACK'd back to queued, got {job['status']}"
        assert job["worker_id"] is None
    finally:
        executor.shutdown(wait=True)


@pytest.mark.asyncio
async def test_sync_drain_completes_within_shutdown_timeout(capture_shutdown_event):
    """Sync handler that finishes inside shutdown_timeout: clean drain."""
    backend = MemoryBackend()
    await backend.initialize()
    registry = JobRegistry()

    done_marker: list[bool] = []

    def quick_sync():
        time.sleep(0.5)
        done_marker.append(True)

    job_id = await _enqueue(backend, registry, quick_sync)

    worker, executor = await _make_worker(registry, backend, shutdown_timeout=10.0)
    try:
        run_task = asyncio.create_task(worker.run(concurrency=1))
        await _wait_for_claim(backend, job_id)

        capture_shutdown_event["event"].set()

        t0 = time.monotonic()
        await asyncio.wait_for(run_task, timeout=10.0)
        elapsed = time.monotonic() - t0

        assert elapsed < 5.0, f"drain took {elapsed:.2f}s"
        assert done_marker == [True]

        job = await backend.get_job(job_id)
        assert job["status"] == "done"
    finally:
        executor.shutdown(wait=True)


@pytest.mark.asyncio
async def test_sync_grace_then_thread_returns_before_grace_expires(
    capture_shutdown_event,
):
    """Sync handler longer than shutdown_timeout but shorter than grace.

    Layout:
        handler runs ~2.0s
        shutdown_timeout = 0.5s
        sync_handler_grace_seconds = 5.0s

    Expected: FORCE_TIMEOUT_PATH fires at ~0.5s, then sync branch waits
    in the grace window and the thread returns at ~2.0s. Total wall
    time ~2.0s, well below 0.5 + 5.0 = 5.5s. The job completes (status
    'done') because Soniq never NACK'd it.
    """
    backend = MemoryBackend()
    await backend.initialize()
    registry = JobRegistry()

    started = asyncio.Event()
    loop = asyncio.get_running_loop()

    def slow_sync():
        loop.call_soon_threadsafe(started.set)
        time.sleep(2.0)

    job_id = await _enqueue(backend, registry, slow_sync)

    worker, executor = await _make_worker(
        registry,
        backend,
        shutdown_timeout=0.5,
        sync_handler_grace_seconds=5.0,
    )
    try:
        run_task = asyncio.create_task(worker.run(concurrency=1))
        await asyncio.wait_for(started.wait(), timeout=2.0)

        capture_shutdown_event["event"].set()

        t0 = time.monotonic()
        await asyncio.wait_for(run_task, timeout=10.0)
        elapsed = time.monotonic() - t0

        # Total wall time tracks the *handler*, not the grace ceiling:
        # we should finish around 2.0s, not 5.5s.
        assert elapsed < 4.0, (
            f"sync grace window over-waited: elapsed={elapsed:.2f}s; "
            f"expected ~2.0s (grace upper bound is 5.5s but handler is 2.0s)"
        )

        job = await backend.get_job(job_id)
        assert (
            job["status"] == "done"
        ), f"sync handler completed; row should be done, got {job['status']}"
    finally:
        executor.shutdown(wait=True)


@pytest.mark.asyncio
async def test_nack_job_is_idempotent_and_only_acts_on_processing():
    """nack_job should be a no-op on already-completed or already-queued
    rows. The locked WHERE clause `id=$1 AND status='processing'` is the
    contract surface.
    """
    backend = MemoryBackend()
    await backend.initialize()
    registry = JobRegistry()

    async def noop():
        pass

    # Case 1: queued job - nack should be no-op.
    job_id = await _enqueue(backend, registry, noop)
    await backend.nack_job(job_id)
    job = await backend.get_job(job_id)
    assert job["status"] == "queued"
    assert job.get("worker_id") in (None, "")

    # Case 2: simulate a 'done' job - nack should be no-op.
    backend._jobs[job_id]["status"] = "done"
    backend._jobs[job_id]["worker_id"] = "fake-worker"
    await backend.nack_job(job_id)
    job = await backend.get_job(job_id)
    assert job["status"] == "done"
    assert job["worker_id"] == "fake-worker"

    # Case 3: an actually-processing job flips back; calling twice is safe.
    backend._jobs[job_id]["status"] = "processing"
    backend._jobs[job_id]["worker_id"] = "claimed-worker"
    backend._jobs[job_id]["attempts"] = 2
    backend._jobs[job_id]["last_error"] = "prev failure"

    await backend.nack_job(job_id)
    job = await backend.get_job(job_id)
    assert job["status"] == "queued"
    assert job["worker_id"] is None
    # attempts and last_error must be preserved (locked-field contract).
    assert job["attempts"] == 2
    assert job["last_error"] == "prev failure"

    # Idempotency: second call is a no-op now that status != processing.
    await backend.nack_job(job_id)
    job = await backend.get_job(job_id)
    assert job["status"] == "queued"
    assert job["attempts"] == 2
