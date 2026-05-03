"""
Tests that Soniq handles timezones correctly for users.

Users should be able to pass datetimes in any timezone (or naive local time)
and the framework converts to UTC for storage as TIMESTAMP WITH TIME ZONE.
"""

from datetime import datetime, timedelta, timezone

import pytest

from soniq.core.queue import _normalize_scheduled_time


class TestNormalizeScheduledTime:
    """Verify _normalize_scheduled_time handles all timezone scenarios."""

    def test_none_returns_none(self):
        assert _normalize_scheduled_time(None) is None

    def test_timedelta_returns_utc_aware(self):
        result = _normalize_scheduled_time(timedelta(seconds=30))
        assert result.tzinfo == timezone.utc
        utc_now = datetime.now(timezone.utc)
        diff = abs((result - utc_now).total_seconds() - 30)
        assert diff < 2

    def test_int_seconds_returns_utc_aware(self):
        result = _normalize_scheduled_time(60)
        assert result.tzinfo == timezone.utc
        diff = abs((result - datetime.now(timezone.utc)).total_seconds() - 60)
        assert diff < 2

    def test_utc_aware_datetime_preserved(self):
        """UTC-aware datetime should remain UTC-aware."""
        dt = datetime(2025, 6, 15, 14, 30, tzinfo=timezone.utc)
        result = _normalize_scheduled_time(dt)
        assert result.tzinfo == timezone.utc
        assert result == datetime(2025, 6, 15, 14, 30, tzinfo=timezone.utc)

    def test_offset_aware_datetime_converted_to_utc(self):
        """Timezone-aware datetime in non-UTC should be converted to UTC."""
        # EST is UTC-5
        est = timezone(timedelta(hours=-5))
        dt = datetime(2025, 6, 15, 14, 30, tzinfo=est)
        result = _normalize_scheduled_time(dt)
        assert result.tzinfo == timezone.utc
        # 14:30 EST = 19:30 UTC
        assert result == datetime(2025, 6, 15, 19, 30, tzinfo=timezone.utc)

    def test_naive_datetime_rejected(self):
        """Naive datetimes are rejected: they're ambiguous across hosts."""
        local_now = datetime.now()
        with pytest.raises(ValueError, match="timezone-aware"):
            _normalize_scheduled_time(local_now)

    def test_naive_datetime_rejection_message_is_actionable(self):
        """The error tells the user how to fix their call."""
        naive = datetime(2025, 6, 15, 10, 0, 0)
        with pytest.raises(ValueError) as exc_info:
            _normalize_scheduled_time(naive)
        assert "timezone.utc" in str(exc_info.value) or "tzinfo" in str(exc_info.value)
