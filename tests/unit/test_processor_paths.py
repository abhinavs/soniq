"""
Tests for processor.py uncovered paths.

Covers: dict args, corrupted args, no-timeout path, max_attempts exceeded guard,
unregistered job dead-letter, corruption exception handling, hooks.
"""

import pytest

from soniq.core.processor import process_job_via_backend
from soniq.core.registry import JobRegistry
from soniq.testing.memory_backend import MemoryBackend


async def _setup(job_func, args={}, max_attempts=3, attempts_override=None):
    """Helper: create backend, registry, job, return (backend, registry)."""
    backend = MemoryBackend()
    await backend.initialize()
    registry = JobRegistry()
    wrapped = registry.register_job(job_func, name=job_func.__name__)
    job_name = wrapped._soniq_name

    await backend.create_job(
        job_id="job-1",
        job_name=job_name,
        args=args,
        args_hash=None,
        max_attempts=max_attempts,
        priority=100,
        queue="default",
        unique=False,
        dedup_key=None,
        scheduled_at=None,
    )

    # Hack attempts if needed (simulate repeated crashes)
    if attempts_override is not None:
        backend._jobs["job-1"]["attempts"] = attempts_override

    return backend, registry


@pytest.mark.asyncio
async def test_dict_args_are_accepted():
    """When args is already a dict (not JSON string), processor should handle it."""

    async def greet(name: str):
        pass

    backend, registry = await _setup(greet, args={"name": "Alice"})
    result = await process_job_via_backend(backend, registry, queues=["default"])
    assert result is True
    job = await backend.get_job("job-1")
    assert job["status"] == "done"


@pytest.mark.asyncio
async def test_corrupted_args_type_dead_letters():
    """Non-dict/non-string args should dead-letter the job."""

    async def my_task():
        pass

    backend, registry = await _setup(my_task, args=12345)
    result = await process_job_via_backend(backend, registry, queues=["default"])
    assert result is True
    # DLQ Option A: dead-lettered rows move into _dead_letter_jobs and
    # the source row is removed from soniq_jobs.
    assert await backend.get_job("job-1") is None
    assert "job-1" in backend._dead_letter_jobs


@pytest.mark.asyncio
async def test_string_args_are_contract_violation():
    """The backend contract is `args: dict`; a string value is a
    violation and the processor should dead-letter rather than attempt
    JSON parsing. String-args tolerance was removed in 0.0.2."""

    async def my_task():
        pass

    backend, registry = await _setup(my_task, args="not valid json{{{")
    result = await process_job_via_backend(backend, registry, queues=["default"])
    assert result is True
    assert await backend.get_job("job-1") is None
    assert "job-1" in backend._dead_letter_jobs


@pytest.mark.asyncio
async def test_no_timeout_path_executes_directly():
    """Jobs without timeout should execute without asyncio.wait_for."""
    executed = []

    async def simple_task(x: int):
        executed.append(x)

    backend, registry = await _setup(simple_task, args={"x": 42})
    await process_job_via_backend(backend, registry, queues=["default"])
    assert executed == [42]


@pytest.mark.asyncio
async def test_max_attempts_exceeded_guard():
    """Jobs with attempts > max_attempts should be dead-lettered without execution."""
    executed = []

    async def should_not_run():
        executed.append(True)

    backend, registry = await _setup(
        should_not_run, max_attempts=2, attempts_override=5
    )
    result = await process_job_via_backend(backend, registry, queues=["default"])
    assert result is True
    assert executed == []  # Should NOT have been executed
    assert await backend.get_job("job-1") is None
    assert "job-1" in backend._dead_letter_jobs


@pytest.mark.asyncio
async def test_unregistered_job_dead_letters():
    """Jobs not in registry should be dead-lettered."""
    backend = MemoryBackend()
    await backend.initialize()
    registry = JobRegistry()

    await backend.create_job(
        job_id="job-orphan",
        job_name="nonexistent.module.task",
        args={},
        args_hash=None,
        max_attempts=3,
        priority=100,
        queue="default",
        unique=False,
        dedup_key=None,
        scheduled_at=None,
    )

    result = await process_job_via_backend(backend, registry, queues=["default"])
    assert result is True
    assert await backend.get_job("job-orphan") is None
    assert "job-orphan" in backend._dead_letter_jobs


@pytest.mark.asyncio
async def test_before_and_after_hooks_called():
    """Hooks should be called around job execution."""
    hook_calls = []

    async def my_task():
        pass

    async def before_hook(job_name, job_id, attempt):
        hook_calls.append(("before", job_name))

    async def after_hook(job_name, job_id, duration_ms):
        hook_calls.append(("after", job_name))

    backend, registry = await _setup(my_task)
    hooks = {"before_job": [before_hook], "after_job": [after_hook]}
    await process_job_via_backend(backend, registry, queues=["default"], hooks=hooks)

    assert len(hook_calls) == 2
    assert hook_calls[0][0] == "before"
    assert hook_calls[1][0] == "after"


@pytest.mark.asyncio
async def test_on_error_hook_called_on_failure():
    error_info = []

    async def failing_task():
        raise RuntimeError("oops")

    async def error_hook(job_name, job_id, error, attempt):
        error_info.append(error)

    backend, registry = await _setup(failing_task)
    hooks = {"on_error": [error_hook]}
    await process_job_via_backend(backend, registry, queues=["default"], hooks=hooks)

    assert len(error_info) == 1
    assert "oops" in error_info[0]


@pytest.mark.asyncio
async def test_sync_hook_is_supported():
    """Non-async hooks should also work."""
    called = []

    async def my_task():
        pass

    def sync_hook(job_name, job_id, attempt):
        called.append(True)

    backend, registry = await _setup(my_task)
    hooks = {"before_job": [sync_hook]}
    await process_job_via_backend(backend, registry, queues=["default"], hooks=hooks)
    assert called == [True]
