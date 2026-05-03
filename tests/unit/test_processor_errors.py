"""
Tests that job failures carry enough detail to debug in production.

Prior to this PR, `_execute_job_safely` caught bare `Exception` and stored
`str(e)` in `last_error`. The dashboard showed things like "Connection
refused" with no callsite, file, or line. We now capture the full traceback
so operators can read it back from the job record.
"""

import pytest

from soniq.core.processor import process_job_via_backend
from soniq.core.registry import JobRegistry
from soniq.testing.memory_backend import MemoryBackend


def _helper_that_explodes():
    """Named helper so we can assert its name appears in the traceback."""
    raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_last_error_includes_traceback():
    """Failing job stores exception type, message, and traceback frames."""
    backend = MemoryBackend()
    await backend.initialize()
    registry = JobRegistry()

    async def failing_task():
        _helper_that_explodes()

    registry.register_job(failing_task, name=failing_task.__name__, retries=0)
    job_name = failing_task.__name__

    await backend.create_job(
        job_id="err-1",
        job_name=job_name,
        args={},
        args_hash=None,
        max_attempts=1,
        priority=100,
        queue="default",
        unique=False,
        dedup_key=None,
        scheduled_at=None,
    )

    await process_job_via_backend(backend, registry, queues=["default"])

    # DLQ Option A: dead-lettered jobs live in soniq_dead_letter_jobs.
    assert await backend.get_job("err-1") is None
    dlq_row = backend._dead_letter_jobs["err-1"]

    last_error = dlq_row["last_error"]
    assert "RuntimeError" in last_error, last_error
    assert "boom" in last_error, last_error
    assert "_helper_that_explodes" in last_error, last_error
    assert "Traceback" in last_error, last_error


@pytest.mark.asyncio
async def test_last_error_truncated_to_reasonable_size():
    """Tracebacks bounded so one bad job can't bloat the last_error column."""
    backend = MemoryBackend()
    await backend.initialize()
    registry = JobRegistry()

    async def noisy_failure():
        raise RuntimeError("x" * 50000)

    registry.register_job(noisy_failure, name=noisy_failure.__name__, retries=0)
    job_name = noisy_failure.__name__

    await backend.create_job(
        job_id="err-big",
        job_name=job_name,
        args={},
        args_hash=None,
        max_attempts=1,
        priority=100,
        queue="default",
        unique=False,
        dedup_key=None,
        scheduled_at=None,
    )
    await process_job_via_backend(backend, registry, queues=["default"])

    assert await backend.get_job("err-big") is None
    dlq_row = backend._dead_letter_jobs["err-big"]
    assert len(dlq_row["last_error"]) <= 8192
