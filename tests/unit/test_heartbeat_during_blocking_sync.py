"""
Heartbeats keep firing while a sync handler is parked in a thread.

Contract: sync handlers run on a per-instance bounded ``ThreadPoolExecutor``
and the worker task awaits them via ``asyncio.wrap_future``. The event
loop must stay free during that wait so the heartbeat task continues
to update the worker row. If the implementation ever regressed to
running the sync handler on the loop thread (e.g. via a naked call),
heartbeats would stall and the stale-worker sweep would reclaim live
jobs.

This test wires a Worker against MemoryBackend with
``heartbeat_interval=0.1``, dispatches a sync handler that does
``time.sleep(1.0)``, and asserts that several heartbeat updates fire
during that window.
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
    from soniq.utils import signals as signals_mod

    holder: dict = {}
    original = signals_mod.GracefulSignalHandler.setup_signal_handlers

    def patched(self, shutdown_event):
        holder["event"] = shutdown_event
        self.shutdown_event = shutdown_event

    monkeypatch.setattr(
        signals_mod.GracefulSignalHandler, "setup_signal_handlers", patched
    )
    yield holder
    signals_mod.GracefulSignalHandler.setup_signal_handlers = original


class _CountingMemoryBackend(MemoryBackend):
    """MemoryBackend that counts update_heartbeat calls."""

    def __init__(self):
        super().__init__()
        self.heartbeat_calls = 0

    async def update_heartbeat(self, worker_id, metadata=None):
        self.heartbeat_calls += 1
        await super().update_heartbeat(worker_id, metadata=metadata)


@pytest.mark.asyncio
async def test_heartbeats_continue_during_blocking_sync_handler(
    capture_shutdown_event,
):
    backend = _CountingMemoryBackend()
    await backend.initialize()
    registry = JobRegistry()

    settings = SoniqSettings(
        database_url="postgresql://placeholder/_unused",
        shutdown_timeout=5.0,
        sync_handler_grace_seconds=0.0,
        job_timeout=0,
        sync_handler_pool_size=1,
        heartbeat_interval=0.1,
        poll_interval=0.1,
        cleanup_interval=10.0,
    )

    def blocking_sync_handler():
        # Time.sleep on a real OS thread; if this runs on the loop, the
        # heartbeat task will stall and the assertion at the end fails.
        # 1.0s is enough to fit ~10 heartbeat ticks at interval=0.1.
        time.sleep(1.0)

    registry.register_job(blocking_sync_handler, name="blocking_sync_handler")
    await backend.create_job(
        job_id=str(uuid.uuid4()),
        job_name="blocking_sync_handler",
        args={},
        args_hash=None,
        max_attempts=1,
        priority=100,
        queue="default",
        unique=False,
        dedup_key=None,
        scheduled_at=None,
    )

    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="t-sync")
    semaphore = asyncio.Semaphore(1)
    worker = Worker(
        backend=backend,
        registry=registry,
        settings=settings,
        sync_executor=executor,
        sync_pool_semaphore=semaphore,
    )

    async def trigger_shutdown_after_handler_runs():
        # Give the worker time to claim the job, dispatch into the
        # executor, and accumulate heartbeat ticks while time.sleep runs.
        await asyncio.sleep(1.2)
        # Snapshot heartbeat count before signalling shutdown so we
        # measure heartbeats that fired *during* the blocking handler,
        # not afterwards.
        baseline = backend.heartbeat_calls
        capture_shutdown_event["event"].set()
        return baseline

    snapshot_task = asyncio.create_task(trigger_shutdown_after_handler_runs())
    try:
        await asyncio.wait_for(
            worker.run(concurrency=1, queues=["default"]),
            timeout=10.0,
        )
    finally:
        executor.shutdown(wait=True)

    heartbeats_during_block = await snapshot_task

    # Heartbeat interval is 0.1s and we held the sync handler in
    # time.sleep for ~1s, so we expect roughly 10 ticks. Assert at
    # least 5 to leave headroom for scheduling jitter and the small
    # window before the executor task starts.
    assert heartbeats_during_block >= 5, (
        f"only {heartbeats_during_block} heartbeats fired while a sync "
        f"handler was parked in time.sleep(1.0); expected >=5. The event "
        f"loop appears to have been blocked - this is the regression the "
        f"sync-offload contract is meant to prevent."
    )
