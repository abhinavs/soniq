"""
Tests for JobContext injection into job functions.
"""

import uuid

import pytest

from soniq.core.processor import _execute_job_safely


def _make_job_record(args_dict=None):
    if args_dict is None:
        args_dict = {}
    return {
        "id": uuid.UUID("12345678-1234-5678-1234-567812345678"),
        "job_name": "test_module.test_func",
        "args": args_dict,
        "attempts": 2,
        "max_attempts": 5,
        "queue": "emails",
        "worker_id": "worker-abc",
        "scheduled_at": None,
        "created_at": None,
    }


def _make_job_meta(func):
    return {
        "func": func,
        "args_model": None,
        "max_retries": 4,
        "retry_delay": 0,
        "retry_backoff": False,
        "retry_max_delay": None,
        "timeout": None,
    }


@pytest.mark.asyncio
async def test_job_with_context_receives_metadata():
    """A job that declares ctx: JobContext should receive it."""
    from soniq.job import JobContext

    received = {}

    async def my_job(msg: str, ctx: JobContext):
        received["job_id"] = ctx.job_id
        received["attempt"] = ctx.attempt
        received["max_attempts"] = ctx.max_attempts
        received["queue"] = ctx.queue

    job_record = _make_job_record({"msg": "hello"})
    job_meta = _make_job_meta(my_job)

    success, error, _result = await _execute_job_safely(job_record, job_meta)
    assert success is True
    assert received["job_id"] == "12345678-1234-5678-1234-567812345678"
    assert received["attempt"] == 2
    assert received["max_attempts"] == 5
    assert received["queue"] == "emails"


@pytest.mark.asyncio
async def test_job_without_context_works_unchanged():
    """A job that doesn't declare JobContext should work as before."""
    called = False

    async def simple_job(x: int):
        nonlocal called
        called = True

    job_record = _make_job_record({"x": 42})
    job_meta = _make_job_meta(simple_job)

    success, error, _result = await _execute_job_safely(job_record, job_meta)
    assert success is True
    assert called is True


@pytest.mark.asyncio
async def test_context_is_importable_from_top_level():
    """JobContext should be importable from soniq directly."""
    from soniq import JobContext

    assert JobContext is not None
