"""
Tests for retry delay edge cases not covered by test_retry_backoff.py.

Covers: attempt<1 normalization, None delay, empty delay list, negative clamp.
"""

from soniq.core.retry import compute_retry_delay_seconds


def test_attempt_below_one_is_normalized_to_one():
    """When attempt < 1, should be treated as attempt 1."""
    result = compute_retry_delay_seconds(attempt=0, retry_delay=5)
    assert result == 5.0


def test_negative_attempt_is_normalized():
    result = compute_retry_delay_seconds(attempt=-3, retry_delay=10)
    assert result == 10.0


def test_none_delay_returns_zero():
    """retry_delay=None should produce zero delay."""
    result = compute_retry_delay_seconds(attempt=1, retry_delay=None)
    assert result == 0.0


def test_empty_delay_list_returns_zero():
    """Empty list/tuple should produce zero delay."""
    assert compute_retry_delay_seconds(attempt=1, retry_delay=[]) == 0.0
    assert compute_retry_delay_seconds(attempt=1, retry_delay=()) == 0.0


def test_backoff_with_zero_base_uses_one():
    """Backoff with delay=0 should use base=1.0 (2^(attempt-1))."""
    result = compute_retry_delay_seconds(
        attempt=3, retry_delay=0, retry_backoff=True, retry_jitter=False
    )
    assert result == 4.0  # 1.0 * 2^2


def test_backoff_with_none_delay_uses_one():
    result = compute_retry_delay_seconds(
        attempt=2, retry_delay=None, retry_backoff=True, retry_jitter=False
    )
    assert result == 2.0  # 1.0 * 2^1


def test_max_delay_caps_backoff():
    result = compute_retry_delay_seconds(
        attempt=10,
        retry_delay=1,
        retry_backoff=True,
        retry_max_delay=100,
        retry_jitter=False,
    )
    assert result == 100.0


def test_negative_delay_clamped_to_zero():
    """Negative retry_delay should be clamped to 0."""
    result = compute_retry_delay_seconds(attempt=1, retry_delay=-5)
    assert result == 0.0


def test_list_delay_with_backoff():
    """List delay with backoff should use the selected element as base."""
    result = compute_retry_delay_seconds(
        attempt=2, retry_delay=[1, 3, 10], retry_backoff=True, retry_jitter=False
    )
    # attempt=2 → index=1 → delay=3, backoff: 3 * 2^(2-1) = 6
    assert result == 6.0
