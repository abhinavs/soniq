"""
Advisory-lock leader election for maintenance tasks.

Multiple Soniq workers share a database, and each worker would otherwise
run its own copy of periodic maintenance (delete_expired_jobs,
cleanup_stale_workers, recurring scheduler). Each tick is idempotent, but
doing the same work N times per interval is wasted CPU and IO. Leader
election on the Postgres session-scoped advisory lock lets exactly one
worker run a given task per tick; the others skip it until that worker
crashes or releases.

Session-scoped advisory locks release automatically when the owning
connection drops, so a crashed worker does not deadlock its successors.
Callers MUST hold the connection across acquire/release; if the connection
returns to the pool between operations, the lock will be released early.
This module's `with_advisory_lock` context manager pins a connection for
the full duration.

PgBouncer caveat: advisory locks are tied to a session. Session-pooling mode
works. Transaction-pooling mode breaks the lock semantics, so do not use
PgBouncer in transaction mode with this feature.
"""

from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager
from typing import AsyncIterator

# Hash domain separator so a plugin can add its own names without colliding.
_DOMAIN = b"soniq.leadership.v1/"


def advisory_key(name: str) -> int:
    """
    Derive a stable 64-bit signed int from a name.

    Uses blake2b(digest_size=8) so the mapping is deterministic across
    Python processes (unlike built-in hash(), which is salted per-process).
    """
    digest = hashlib.blake2b(_DOMAIN + name.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=True)


@asynccontextmanager
async def with_advisory_lock(backend: object, name: str) -> AsyncIterator[bool]:
    """
    Try to acquire an advisory lock. Yields True if acquired, False otherwise.

    Backends that do not implement `with_advisory_lock` (Memory, SQLite) are
    treated as always-leader: the context manager yields True unconditionally.
    This is correct for single-writer backends and for test backends where
    no other process is racing.
    """
    impl = getattr(backend, "with_advisory_lock", None)
    if impl is None:
        yield True
        return

    async with impl(name) as acquired:
        yield acquired
