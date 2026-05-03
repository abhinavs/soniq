"""
Test helpers exposed from `soniq.testing`.

`wait_until` is the deadline-based polling primitive that replaces fixed
`asyncio.sleep` calls in async tests. `make_app` is a one-liner for
spinning up a `Soniq` against the in-memory backend.
"""

import asyncio
import time
from typing import Any, Awaitable, Callable, Optional, Union


async def wait_until(
    predicate: Callable[[], Union[bool, Awaitable[bool]]],
    *,
    timeout: float = 2.0,
    poll: float = 0.01,
    message: Optional[str] = None,
) -> None:
    """Poll `predicate()` until it becomes truthy or the deadline elapses.

    `predicate` may be sync or async. Polling cadence is `poll` seconds
    (10ms default); total wait caps at `timeout` (2s default). On timeout,
    raises `AssertionError` with `message` if provided, else a generic
    timeout description.

    Example:
        # bad: hopes the worker has finished by now
        await asyncio.sleep(0.5)
        assert job["status"] == "done"

        # good: explicit deadline
        await wait_until(
            lambda: backend.jobs["j1"]["status"] == "done",
            timeout=2.0,
        )
    """
    deadline = time.monotonic() + timeout
    while True:
        result = predicate()
        if asyncio.iscoroutine(result):
            result = await result
        if result:
            return
        if time.monotonic() >= deadline:
            raise AssertionError(
                message
                or f"wait_until timed out after {timeout}s waiting for {predicate!r}"
            )
        await asyncio.sleep(poll)


def make_app(**overrides: Any) -> Any:
    """Construct a `Soniq` instance against the in-memory backend.

    Equivalent to:

        from soniq import Soniq
        from soniq.testing import MemoryBackend
        app = Soniq(backend=MemoryBackend(), **overrides)

    plus the implicit `await app._ensure_initialized()` that callers
    almost always need next. Returned instance is uninitialized; the
    caller does `await app._ensure_initialized()` (or just calls
    `app.enqueue`, which initializes lazily).
    """
    from soniq import Soniq

    return Soniq(backend="memory", **overrides)
