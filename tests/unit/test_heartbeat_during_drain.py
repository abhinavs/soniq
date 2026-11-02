"""
Heartbeats keep firing while a graceful shutdown drains in-flight jobs.

If the heartbeat stopped at the shutdown signal, a drain longer than
heartbeat_timeout would let the stale sweep requeue the job and another
worker run it again while this one is still finishing it.
"""

import asyncio
import uuid

import pytest

from soniq.core.registry import JobRegistry
from soniq.core.worker import Worker
from soniq.settings import SoniqSettings
from soniq.testing.memory_backend import MemoryBackend


class _CountingMemoryBackend(MemoryBackend):
    def __init__(self):
        super().__init__()
        self.heartbeat_calls = 0

    async def update_heartbeat(self, worker_id, metadata=None):
        self.heartbeat_calls += 1
        await super().update_heartbeat(worker_id, metadata=metadata)


@pytest.mark.asyncio
async def test_heartbeats_continue_while_draining():
    backend = _CountingMemoryBackend()
    await backend.initialize()
    registry = JobRegistry()
    settings = SoniqSettings(
        database_url="postgresql://placeholder/_unused",
        shutdown_timeout=5.0,
        heartbeat_interval=0.1,
        poll_interval=0.1,
        cleanup_interval=10.0,
    )
    started = asyncio.Event()

    async def slow_job():
        started.set()
        await asyncio.sleep(1.0)

    registry.register_job(slow_job, name="slow_job")
    await backend.create_job(
        job_id=str(uuid.uuid4()),
        job_name="slow_job",
        args={},
        args_hash=None,
        max_attempts=1,
        priority=100,
        queue="default",
        unique=False,
        dedup_key=None,
        scheduled_at=None,
    )
    shutdown = asyncio.Event()
    worker = Worker(backend=backend, registry=registry, settings=settings)
    run = asyncio.create_task(
        worker.run(concurrency=1, queues=["default"], shutdown_event=shutdown)
    )

    await asyncio.wait_for(started.wait(), timeout=5.0)
    shutdown.set()
    baseline = backend.heartbeat_calls
    await asyncio.wait_for(run, timeout=10.0)

    # About 10 ticks fit in the 1s drain at interval 0.1.
    assert backend.heartbeat_calls - baseline >= 5
