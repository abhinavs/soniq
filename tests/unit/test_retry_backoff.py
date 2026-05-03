import random

import pytest

from soniq.core.retry import compute_retry_delay_seconds, set_rng


@pytest.fixture(autouse=True)
def _reset_rng():
    """Each test gets a fresh deterministic RNG; reset to default after."""
    set_rng(random.Random(0xE1E8A0F))
    yield
    set_rng(random.Random())


def test_retry_delay_fixed():
    # Fixed delay, no backoff, no jitter applied.
    assert (
        compute_retry_delay_seconds(attempt=1, retry_delay=2, retry_backoff=False) == 2
    )
    assert (
        compute_retry_delay_seconds(attempt=3, retry_delay=2, retry_backoff=False) == 2
    )


def test_retry_delay_list():
    assert (
        compute_retry_delay_seconds(
            attempt=1, retry_delay=[1, 5, 10], retry_backoff=False
        )
        == 1
    )
    assert (
        compute_retry_delay_seconds(
            attempt=2, retry_delay=[1, 5, 10], retry_backoff=False
        )
        == 5
    )
    assert (
        compute_retry_delay_seconds(
            attempt=5, retry_delay=[1, 5, 10], retry_backoff=False
        )
        == 10
    )


def test_retry_backoff_without_jitter_is_deterministic():
    assert (
        compute_retry_delay_seconds(
            attempt=1, retry_delay=1, retry_backoff=True, retry_jitter=False
        )
        == 1
    )
    assert (
        compute_retry_delay_seconds(
            attempt=2, retry_delay=1, retry_backoff=True, retry_jitter=False
        )
        == 2
    )
    assert (
        compute_retry_delay_seconds(
            attempt=3, retry_delay=1, retry_backoff=True, retry_jitter=False
        )
        == 4
    )


def test_retry_backoff_with_jitter_is_in_full_jitter_bounds():
    # At attempt=1, deterministic ceiling is base=1 → delay ∈ [0.5, 1.0].
    for _ in range(50):
        d = compute_retry_delay_seconds(
            attempt=1, retry_delay=1, retry_backoff=True, retry_jitter=True
        )
        assert 0.5 <= d <= 1.0

    # At attempt=4, deterministic ceiling is 8 → delay ∈ [4, 8].
    for _ in range(50):
        d = compute_retry_delay_seconds(
            attempt=4, retry_delay=1, retry_backoff=True, retry_jitter=True
        )
        assert 4.0 <= d <= 8.0


def test_jitter_respects_max_delay_cap():
    # Max cap is applied before jitter: high attempt with cap=5 → delay ∈ [2.5, 5].
    for _ in range(50):
        d = compute_retry_delay_seconds(
            attempt=20,
            retry_delay=1,
            retry_backoff=True,
            retry_max_delay=5,
            retry_jitter=True,
        )
        assert 2.5 <= d <= 5.0


def test_retry_backoff_max_delay_without_jitter():
    assert (
        compute_retry_delay_seconds(
            attempt=4,
            retry_delay=2,
            retry_backoff=True,
            retry_max_delay=5,
            retry_jitter=False,
        )
        == 5
    )


def test_huge_attempt_number_does_not_overflow():
    # Should never raise OverflowError for absurd attempt counts.
    d = compute_retry_delay_seconds(
        attempt=10_000,
        retry_delay=1,
        retry_backoff=True,
        retry_max_delay=300,
        retry_jitter=True,
    )
    assert 0 <= d <= 300


def test_rng_injection_makes_results_reproducible():
    set_rng(random.Random(42))
    first = [
        compute_retry_delay_seconds(
            attempt=3, retry_delay=1, retry_backoff=True, retry_jitter=True
        )
        for _ in range(5)
    ]
    set_rng(random.Random(42))
    second = [
        compute_retry_delay_seconds(
            attempt=3, retry_delay=1, retry_backoff=True, retry_jitter=True
        )
        for _ in range(5)
    ]
    assert first == second


def test_jitter_ignored_when_backoff_is_false():
    # With retry_backoff=False, retry_jitter has no effect.
    d1 = compute_retry_delay_seconds(
        attempt=3, retry_delay=7, retry_backoff=False, retry_jitter=True
    )
    d2 = compute_retry_delay_seconds(
        attempt=3, retry_delay=7, retry_backoff=False, retry_jitter=False
    )
    assert d1 == d2 == 7
