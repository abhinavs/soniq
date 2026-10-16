"""
Test-tier utilities for Soniq.

The contents of this package are intended for tests, examples, and
quick scripts - not for production. Living under a clearly-named
package signals that boundary at the import site:

    from soniq.testing import MemoryBackend, drain, make_app, wait_until

The shipped helpers:

- `MemoryBackend`: in-memory `StorageBackend` implementation. No
  persistence, no concurrency contention beyond a single asyncio.Lock.
  Suitable for unit tests; not for production.
- `make_app`: convenience constructor for a `Soniq` wired up against
  `MemoryBackend`. Skips the database boot dance.
- `drain`: run every queued job in the test's event loop and return
  the results.
- `wait_until`: poll-with-deadline replacement for `await
  asyncio.sleep(N)`. Tests that race against scheduled work or
  background tasks should use this instead of fixed sleeps.
"""

from .helpers import drain, make_app, wait_until
from .memory_backend import MemoryBackend

__all__ = ["MemoryBackend", "drain", "make_app", "wait_until"]
