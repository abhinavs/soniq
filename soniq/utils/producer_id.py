"""
Resolve the producer_id stamped on every enqueued row.

'Who enqueued this poison message?' is the first question oncall asks
once queues cross repo boundaries. The stamp is small (~50 bytes),
nullable in storage, and resolved once per process when the configured
value is the literal sentinel ``"auto"``.
"""

from __future__ import annotations

import os
import platform
import sys
from typing import Optional

_cached: Optional[str] = None


def _auto_producer_id() -> str:
    """Compose <hostname>:<pid>:<argv0> for the running process.

    argv0 is basenamed for readability and to avoid leaking absolute
    paths in dashboards. The result is cached for the process lifetime.
    """
    hostname = platform.node() or "unknown-host"
    pid = os.getpid()
    argv0 = "python"
    try:
        if sys.argv and sys.argv[0]:
            argv0 = os.path.basename(sys.argv[0]) or argv0
    except Exception:
        pass
    return f"{hostname}:{pid}:{argv0}"


def resolve_producer_id(configured: str) -> str:
    """Map a configured producer_id setting to the value stamped on rows.

    The literal sentinel ``"auto"`` triggers the host/pid/argv0
    composition; anything else is returned verbatim.

    Caches the auto-composed value for the process lifetime; explicit
    values are returned on every call (cheap str passthrough).
    """
    if configured != "auto":
        return configured
    global _cached
    if _cached is None:
        _cached = _auto_producer_id()
    return _cached


def _reset_cache_for_tests() -> None:
    global _cached
    _cached = None
