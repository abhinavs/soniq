"""
Tests for queue.py validation and time normalization.

Covers: Pydantic validation failure path, all _normalize_scheduled_time branches.
"""

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import BaseModel

from soniq.core.queue import _normalize_scheduled_time, _validate_job_arguments

# --- _validate_job_arguments ---


class UserArgs(BaseModel):
    email: str
    count: int


def test_validate_passes_with_valid_args():
    meta = {"args_model": UserArgs}
    # Should not raise
    _validate_job_arguments("my_job", meta, {"email": "a@b.com", "count": 5})


def test_validate_raises_on_invalid_args():
    meta = {"args_model": UserArgs}
    with pytest.raises(ValueError, match="Invalid arguments for job"):
        _validate_job_arguments(
            "my_job", meta, {"email": "a@b.com", "count": "not_int"}
        )


def test_validate_raises_on_missing_required_field():
    meta = {"args_model": UserArgs}
    with pytest.raises(ValueError, match="Invalid arguments for job"):
        _validate_job_arguments("my_job", meta, {"email": "a@b.com"})


def test_validate_skips_when_no_model():
    meta = {}
    # Should not raise — no validation model
    _validate_job_arguments("my_job", meta, {"anything": "goes"})


# --- _normalize_scheduled_time ---


def test_normalize_none_returns_none():
    assert _normalize_scheduled_time(None) is None


def test_normalize_timedelta():
    result = _normalize_scheduled_time(timedelta(hours=1))
    assert result.tzinfo is not None
    # Should be ~1 hour from now
    diff = result - datetime.now(timezone.utc)
    assert 3500 < diff.total_seconds() < 3700


def test_normalize_numeric_seconds():
    result = _normalize_scheduled_time(300)
    assert result.tzinfo is not None
    diff = result - datetime.now(timezone.utc)
    assert 290 < diff.total_seconds() < 310


def test_normalize_float_seconds():
    result = _normalize_scheduled_time(60.5)
    assert result.tzinfo is not None
    diff = result - datetime.now(timezone.utc)
    assert 55 < diff.total_seconds() < 66


def test_normalize_timezone_aware_datetime():
    """Timezone-aware datetime should be converted to UTC."""
    from datetime import timezone as tz

    eastern = tz(timedelta(hours=-5))
    dt = datetime(2025, 6, 15, 12, 0, 0, tzinfo=eastern)
    result = _normalize_scheduled_time(dt)
    assert result.tzinfo == timezone.utc
    assert result.hour == 17  # 12 EST → 17 UTC


def test_normalize_naive_datetime_rejected():
    """Naive datetime is ambiguous across hosts and must be rejected explicitly."""
    dt = datetime(2025, 6, 15, 12, 0, 0)
    with pytest.raises(ValueError, match="timezone-aware"):
        _normalize_scheduled_time(dt)


def test_normalize_aware_datetime_still_accepted():
    """Regression guard: timezone-aware datetimes continue to work."""
    dt = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    result = _normalize_scheduled_time(dt)
    assert result == dt
    assert result.tzinfo == timezone.utc
