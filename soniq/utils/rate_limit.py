"""
Rate-limited warning helper.

Used by ``Soniq.enqueue`` in ``warn`` mode so a producer that fires
the same unknown task name in a loop does not flood logs. Keyed on
the task name; emits at most once per ``(key, ttl_window)`` pair
per process.

The contract is intentionally implementation-defined: callers MUST
NOT depend on the exact dedup window or eviction policy. The
LRU+TTL can be swapped for, say, a token bucket without breaking a
public guarantee.

Defaults: per-process LRU of 1024 keys with a 1-hour TTL. Both are
overridable via constructor args, primarily for tests.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import Optional


class RateLimitedWarner:
    """Per-process LRU+TTL deduplicator for warning messages."""

    def __init__(self, maxsize: int = 1024, ttl_seconds: float = 3600.0):
        self._maxsize = maxsize
        self._ttl = ttl_seconds
        # Maps key -> last_emit_monotonic_ts. OrderedDict gives O(1) LRU
        # eviction by move_to_end / popitem(last=False).
        self._seen: OrderedDict[str, float] = OrderedDict()

    def should_warn(self, key: str, now: Optional[float] = None) -> bool:
        """Return True iff this is the first call for ``key`` in the
        current TTL window. Side effect: records the call so subsequent
        calls within the window return False.
        """
        ts = now if now is not None else time.monotonic()
        last = self._seen.get(key)
        if last is not None and (ts - last) < self._ttl:
            # Still in the dedup window. Refresh LRU position so this
            # key stays warm; do not emit.
            self._seen.move_to_end(key)
            return False
        self._seen[key] = ts
        self._seen.move_to_end(key)
        if len(self._seen) > self._maxsize:
            # Evict the oldest entry. After eviction, that key can
            # legitimately warn again next time it appears.
            self._seen.popitem(last=False)
        return True

    def reset(self) -> None:
        """Clear the dedup state. Test-only; production code should rely
        on the TTL/LRU eviction."""
        self._seen.clear()


# Module-level singleton used by Soniq.enqueue for the "warn" mode.
# Swapping this requires no public API change because callers go
# through `default_warner()`.
_default_warner: Optional[RateLimitedWarner] = None


def default_warner() -> RateLimitedWarner:
    global _default_warner
    if _default_warner is None:
        _default_warner = RateLimitedWarner()
    return _default_warner


def _reset_default_warner_for_tests() -> None:
    """Reset the module-level singleton. Used by test fixtures so one
    test's warn-mode emissions do not silence the next test's."""
    global _default_warner
    _default_warner = None
