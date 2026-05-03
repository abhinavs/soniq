"""
Tests for job execution timeout enforcement.

Ensures jobs that exceed their timeout are treated as failures,
retried normally, and eventually dead-lettered.
"""

import asyncio

import pytest

from soniq.core.processor import _execute_job_safely


def _make_job_record(args_dict=None):
    if args_dict is None:
        args_dict = {}
    return {
        "id": "test-job-id",
        "job_name": "test.job",
        "args": args_dict,
        "attempts": 0,
        "max_attempts": 3,
    }


def _make_job_meta(func, timeout=None):
    return {
        "func": func,
        "args_model": None,
        "max_retries": 3,
        "retry_delay": 0,
        "retry_backoff": False,
        "retry_max_delay": None,
        "timeout": timeout,
    }


@pytest.mark.asyncio
async def test_job_exceeding_timeout_is_failed():
    """A job that sleeps longer than its timeout should be marked as failed."""

    async def slow_job():
        await asyncio.sleep(5)

    job_record = _make_job_record()
    job_meta = _make_job_meta(slow_job, timeout=0.1)

    success, error_msg, _result = await _execute_job_safely(job_record, job_meta)
    assert success is False
    assert "timed out" in error_msg.lower()


@pytest.mark.asyncio
async def test_job_within_timeout_succeeds():
    """A job that completes within its timeout should succeed normally."""

    async def fast_job():
        await asyncio.sleep(0.01)

    job_record = _make_job_record()
    job_meta = _make_job_meta(fast_job, timeout=5.0)

    success, error_msg, _result = await _execute_job_safely(job_record, job_meta)
    assert success is True
    assert error_msg is None


@pytest.mark.asyncio
async def test_per_job_timeout_overrides_global(monkeypatch):
    """Per-job timeout from job_meta should be used over the global setting."""

    async def slow_job():
        await asyncio.sleep(5)

    # Set global timeout to something large
    from soniq.settings import get_settings

    settings = get_settings()
    original = settings.job_timeout
    monkeypatch.setattr(settings, "job_timeout", 999.0)

    try:
        job_record = _make_job_record()
        # Per-job timeout is very short — should trigger
        job_meta = _make_job_meta(slow_job, timeout=0.1)

        success, error_msg, _result = await _execute_job_safely(job_record, job_meta)
        assert success is False
        assert "timed out" in error_msg.lower()
    finally:
        monkeypatch.setattr(settings, "job_timeout", original)


@pytest.mark.asyncio
async def test_no_timeout_when_set_to_none():
    """When timeout is None (both per-job and global), no timeout is enforced."""
    completed = False

    async def quick_job():
        nonlocal completed
        await asyncio.sleep(0.05)
        completed = True

    job_record = _make_job_record()
    job_meta = _make_job_meta(quick_job, timeout=None)

    success, error_msg, _result = await _execute_job_safely(job_record, job_meta)
    assert success is True
    assert completed is True
