"""
Tests for error classification in _execute_job_safely.

Ensures TypeError/AttributeError from job logic are retried (not dead-lettered),
while actual data corruption (non-dict args, invalid Pydantic) is dead-lettered.
"""

import pytest

from soniq.core.processor import _execute_job_safely


def _make_job_record(args_dict=None):
    """Create a minimal job record for testing."""
    if args_dict is None:
        args_dict = {}
    return {
        "id": "test-job-id",
        "job_name": "test.job",
        "args": args_dict,
        "attempts": 0,
        "max_attempts": 3,
    }


def _make_job_meta(func, args_model=None):
    """Create minimal job metadata for testing."""
    return {
        "func": func,
        "args_model": args_model,
        "max_retries": 3,
        "retry_delay": 0,
        "retry_backoff": False,
        "retry_max_delay": None,
    }


@pytest.mark.asyncio
async def test_typeerror_with_parameter_in_message_is_retried():
    """
    A TypeError from job logic that happens to contain 'parameter' in the
    message must be retried, NOT dead-lettered as corruption.

    This is the core bug: the old code string-matched on 'argument'/'parameter'
    and would send this to dead letter.
    """

    async def bad_job():
        raise TypeError("NoneType has no attribute 'parameter_count'")

    job_record = _make_job_record()
    job_meta = _make_job_meta(bad_job)

    # This should return (False, error_message) for retry, NOT raise ValueError
    success, error_msg, _result = await _execute_job_safely(job_record, job_meta)
    assert success is False
    assert "parameter_count" in error_msg


@pytest.mark.asyncio
async def test_attributeerror_with_argument_in_message_is_retried():
    """
    An AttributeError from job logic containing 'argument' in the message
    must be retried, not dead-lettered.
    """

    async def bad_job():
        raise AttributeError("'str' object has no attribute 'argument_parser'")

    job_record = _make_job_record()
    job_meta = _make_job_meta(bad_job)

    success, error_msg, _result = await _execute_job_safely(job_record, job_meta)
    assert success is False
    assert "argument_parser" in error_msg


@pytest.mark.asyncio
async def test_regular_typeerror_from_job_is_retried():
    """Regular TypeError from job logic (no 'argument'/'parameter') is retried."""

    async def bad_job():
        raise TypeError("unsupported operand type(s) for +: 'int' and 'str'")

    job_record = _make_job_record()
    job_meta = _make_job_meta(bad_job)

    success, error_msg, _result = await _execute_job_safely(job_record, job_meta)
    assert success is False
    assert "unsupported operand" in error_msg


@pytest.mark.asyncio
async def test_non_dict_args_raises_valueerror():
    """Non-dict args is a backend contract violation and must raise
    ValueError so the processor dead-letters the job."""
    job_record = {
        "id": "test-job-id",
        "job_name": "test.job",
        "args": "not a dict",
        "attempts": 0,
        "max_attempts": 3,
    }

    async def good_job():
        pass

    job_meta = _make_job_meta(good_job)

    with pytest.raises(ValueError, match="Backend contract violation"):
        await _execute_job_safely(job_record, job_meta)


@pytest.mark.asyncio
async def test_pydantic_validation_failure_raises_valueerror():
    """Pydantic validation failure should raise ValueError (dead-letter path)."""
    from pydantic import BaseModel

    class MyArgs(BaseModel):
        count: int

    async def good_job(count: int):
        pass

    # Pass string where int is expected
    job_record = _make_job_record({"count": "not_a_number_at_all"})
    job_meta = _make_job_meta(good_job, args_model=MyArgs)

    # Pydantic v2 actually coerces "not_a_number_at_all" to fail validation
    # This should raise ValueError from the validation block
    with pytest.raises(ValueError, match="Corrupted argument data"):
        await _execute_job_safely(job_record, job_meta)


@pytest.mark.asyncio
async def test_successful_job_returns_true():
    """A successful job returns (True, None)."""

    async def good_job(msg="hello"):
        pass

    job_record = _make_job_record({"msg": "hello"})
    job_meta = _make_job_meta(good_job)

    success, error_msg, _result = await _execute_job_safely(job_record, job_meta)
    assert success is True
    assert error_msg is None


@pytest.mark.asyncio
async def test_generic_exception_from_job_is_retried():
    """A generic Exception from job logic is retried."""

    async def bad_job():
        raise RuntimeError("external service unavailable")

    job_record = _make_job_record()
    job_meta = _make_job_meta(bad_job)

    success, error_msg, _result = await _execute_job_safely(job_record, job_meta)
    assert success is False
    assert "external service unavailable" in error_msg
