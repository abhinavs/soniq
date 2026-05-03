"""
Tests for the rate-limited warn helper used by Soniq.enqueue.

The contract is implementation-defined (see soniq/utils/rate_limit.py
docstring): tests assert the observable behaviour - first call emits,
subsequent calls within the window suppress, expiry re-enables - but
do not lock the exact algorithm.
"""

from __future__ import annotations

import logging

import pytest

from soniq.utils.rate_limit import RateLimitedWarner, _reset_default_warner_for_tests


class TestRateLimitedWarner:
    def test_first_call_emits(self):
        w = RateLimitedWarner()
        assert w.should_warn("a.b") is True

    def test_second_call_within_ttl_suppresses(self):
        w = RateLimitedWarner(ttl_seconds=60)
        assert w.should_warn("a.b") is True
        assert w.should_warn("a.b") is False
        assert w.should_warn("a.b") is False

    def test_different_keys_emit_independently(self):
        w = RateLimitedWarner()
        assert w.should_warn("a.b") is True
        assert w.should_warn("c.d") is True
        # Re-using either is suppressed.
        assert w.should_warn("a.b") is False
        assert w.should_warn("c.d") is False

    def test_repeated_calls_do_not_flood(self):
        """Smoke test against the production default: one call out of
        many emits."""
        w = RateLimitedWarner()
        emits = sum(1 for _ in range(100) if w.should_warn("noisy.task"))
        assert emits == 1

    def test_ttl_expiry_re_enables_warning(self):
        w = RateLimitedWarner(ttl_seconds=60)
        # Pin "now" so we control the clock without sleeping.
        assert w.should_warn("a.b", now=100.0) is True
        # 30s later: still inside the 60s window.
        assert w.should_warn("a.b", now=130.0) is False
        # 70s later: outside the window, should emit again.
        assert w.should_warn("a.b", now=170.0) is True

    def test_lru_eviction_recycles_oldest(self):
        """When the LRU is full, the oldest entry is evicted; that key
        can warn again next time it appears."""
        w = RateLimitedWarner(maxsize=2, ttl_seconds=3600)
        assert w.should_warn("a", now=1.0) is True
        assert w.should_warn("b", now=2.0) is True
        # Touch "b" to make it MRU; "a" becomes the oldest.
        assert w.should_warn("b", now=3.0) is False  # in-window dedup
        # Adding a third key evicts the oldest ("a").
        assert w.should_warn("c", now=4.0) is True
        # "a" was evicted; it can warn again.
        assert w.should_warn("a", now=5.0) is True
        # Adding "a" evicts "b" (now the oldest).
        assert w.should_warn("b", now=6.0) is True

    def test_reset_clears_state(self):
        w = RateLimitedWarner()
        w.should_warn("a")
        w.reset()
        assert w.should_warn("a") is True


# ---------------------------------------------------------------------------
# Integration with Soniq.enqueue
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_default_warner():
    _reset_default_warner_for_tests()
    yield
    _reset_default_warner_for_tests()


@pytest.mark.asyncio
async def test_warn_mode_does_not_flood_on_repeated_unknown(caplog):
    """Many enqueues of the same unknown name in `warn` mode must
    produce far fewer than N warning records."""
    import os

    from soniq.testing import make_app
    from tests.db_utils import TEST_DATABASE_URL

    os.environ.setdefault("SONIQ_DATABASE_URL", TEST_DATABASE_URL)
    app = make_app(enqueue_validation="warn")

    with caplog.at_level(logging.WARNING, logger="soniq.app"):
        for _ in range(50):
            await app.enqueue("billing.flood_me", args={})

    flood_warnings = [r for r in caplog.records if "billing.flood_me" in r.message]
    # Implementation-defined dedup; assert "not flooded" rather than
    # "exactly one." The default LRU+TTL gives one record for fresh
    # state, but the contract only requires that the count is
    # substantially below the call count.
    assert 1 <= len(flood_warnings) <= 5, (
        f"expected rate-limited warnings (~1, at most a few); "
        f"got {len(flood_warnings)}"
    )


@pytest.mark.asyncio
async def test_warn_mode_emits_for_distinct_unknown_names(caplog):
    """Distinct unknown names each get their own warning."""
    import os

    from soniq.testing import make_app
    from tests.db_utils import TEST_DATABASE_URL

    os.environ.setdefault("SONIQ_DATABASE_URL", TEST_DATABASE_URL)
    app = make_app(enqueue_validation="warn")

    with caplog.at_level(logging.WARNING, logger="soniq.app"):
        await app.enqueue("a.first", args={})
        await app.enqueue("a.second", args={})
        await app.enqueue("a.first", args={})  # dedup

    messages = [r.message for r in caplog.records]
    assert any("a.first" in m for m in messages)
    assert any("a.second" in m for m in messages)
    # Total warnings == 2 (one per distinct name; the third call dedups).
    flood_warnings = [
        r for r in caplog.records if "is not registered locally" in r.message
    ]
    assert len(flood_warnings) == 2
