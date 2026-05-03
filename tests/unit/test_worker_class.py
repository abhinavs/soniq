"""
Tests for the extracted Worker class.
"""

import inspect

import pytest


def test_worker_importable():
    from soniq.core.worker import Worker

    assert Worker is not None


def test_worker_takes_backend_and_registry():
    from soniq.core.worker import Worker

    sig = inspect.signature(Worker.__init__)
    params = list(sig.parameters.keys())
    assert "backend" in params
    assert "registry" in params


def test_worker_has_run_method():
    from soniq.core.worker import Worker

    assert hasattr(Worker, "run")
    assert asyncio_iscoroutinefunction_safe(Worker.run)


def test_worker_has_run_once_method():
    from soniq.core.worker import Worker

    assert hasattr(Worker, "run_once")
    assert asyncio_iscoroutinefunction_safe(Worker.run_once)


def asyncio_iscoroutinefunction_safe(func):
    import inspect

    return inspect.iscoroutinefunction(func)


@pytest.mark.asyncio
async def test_worker_run_once_processes_job():
    """Worker.run_once should process a job via MemoryBackend."""
    import uuid

    from soniq.core.registry import JobRegistry
    from soniq.core.worker import Worker
    from soniq.testing.memory_backend import MemoryBackend

    backend = MemoryBackend()
    registry = JobRegistry()
    executed = []

    async def my_task(msg: str):
        executed.append(msg)

    registry.register_job(my_task, name="my_task")
    job_name = "my_task"
    await backend.create_job(
        job_id=str(uuid.uuid4()),
        job_name=job_name,
        args={"msg": "from worker"},
        args_hash=None,
        max_attempts=3,
        priority=100,
        queue="default",
        unique=False,
        dedup_key=None,
        scheduled_at=None,
    )

    worker = Worker(backend=backend, registry=registry)
    result = await worker.run_once(queues=["default"])

    assert result is True
    assert executed == ["from worker"]
