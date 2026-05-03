"""Tests for the Middleware Protocol and chain composition.

Verifies the three properties the protocol promises:
1. Order: middleware run outermost-first on the way in, innermost-last
   on the way out, matching the ASGI/Django ordering most users
   already know.
2. Wrap semantics: a middleware can read or transform the handler's
   return value, and short-circuit by returning before calling
   ``call_next``.
3. Exception propagation: handler errors travel back up through every
   middleware unchanged so wrappers can catch / log / re-raise.

The tests exercise the chain in isolation (no Soniq, no backend) and
end-to-end via ``Soniq(backend="memory").run_worker(run_once=True)``
to pin both the composition primitive and the integration into the
processor.
"""

from __future__ import annotations

from typing import List

import pytest

from soniq import Soniq
from soniq.core.middleware import build_chain
from soniq.job import JobContext


def _make_ctx(name: str = "demo.task") -> JobContext:
    return JobContext(
        job_id="00000000-0000-0000-0000-000000000000",
        job_name=name,
        attempt=1,
        max_attempts=3,
        queue="default",
        worker_id="",
    )


# ---------------------------------------------------------------------------
# Pure chain composition
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_chain_executes_in_order():
    events: List[str] = []

    async def handler(ctx: JobContext) -> str:
        events.append("handler")
        return "result"

    async def outer(ctx: JobContext, call_next):
        events.append("outer:before")
        out = await call_next(ctx)
        events.append("outer:after")
        return out

    async def inner(ctx: JobContext, call_next):
        events.append("inner:before")
        out = await call_next(ctx)
        events.append("inner:after")
        return out

    chain = build_chain([outer, inner], handler)
    result = await chain(_make_ctx())

    assert result == "result"
    assert events == [
        "outer:before",
        "inner:before",
        "handler",
        "inner:after",
        "outer:after",
    ]


@pytest.mark.asyncio
async def test_empty_middleware_returns_handler_unchanged():
    async def handler(ctx: JobContext) -> int:
        return 42

    chain = build_chain([], handler)
    assert chain is handler
    assert await chain(_make_ctx()) == 42


@pytest.mark.asyncio
async def test_middleware_can_transform_return_value():
    async def handler(ctx: JobContext) -> int:
        return 10

    async def doubler(ctx: JobContext, call_next):
        out = await call_next(ctx)
        return out * 2

    chain = build_chain([doubler], handler)
    assert await chain(_make_ctx()) == 20


@pytest.mark.asyncio
async def test_middleware_can_short_circuit():
    handler_called = False

    async def handler(ctx: JobContext) -> str:
        nonlocal handler_called
        handler_called = True
        return "real"

    async def gate(ctx: JobContext, call_next):
        if ctx.job_name == "blocked":
            return "intercepted"
        return await call_next(ctx)

    chain = build_chain([gate], handler)
    assert await chain(_make_ctx(name="blocked")) == "intercepted"
    assert handler_called is False
    assert await chain(_make_ctx(name="ok")) == "real"
    assert handler_called is True


@pytest.mark.asyncio
async def test_handler_exception_propagates_through_middleware():
    seen: List[str] = []

    class Boom(RuntimeError):
        pass

    async def handler(ctx: JobContext):
        raise Boom("kaboom")

    async def observer(ctx: JobContext, call_next):
        try:
            return await call_next(ctx)
        except Boom:
            seen.append("caught")
            raise

    chain = build_chain([observer], handler)
    with pytest.raises(Boom):
        await chain(_make_ctx())
    assert seen == ["caught"]


# ---------------------------------------------------------------------------
# Integration with Soniq.run_worker
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_app_middleware_wraps_handler_dispatch():
    app = Soniq(backend="memory")
    events: List[str] = []

    @app.middleware
    async def trace(ctx: JobContext, call_next):
        events.append(f"before:{ctx.job_name}")
        result = await call_next(ctx)
        events.append(f"after:{ctx.job_name}")
        return result

    @app.job(name="demo.greet")
    async def greet(name: str) -> None:
        events.append(f"handler:{name}")

    await app.enqueue("demo.greet", args={"name": "world"})
    await app.run_worker(run_once=True)
    await app.close()

    assert events == ["before:demo.greet", "handler:world", "after:demo.greet"]


@pytest.mark.asyncio
async def test_multiple_middleware_run_outermost_first():
    app = Soniq(backend="memory")
    events: List[str] = []

    @app.middleware
    async def outer(ctx: JobContext, call_next):
        events.append("outer:before")
        out = await call_next(ctx)
        events.append("outer:after")
        return out

    @app.middleware
    async def inner(ctx: JobContext, call_next):
        events.append("inner:before")
        out = await call_next(ctx)
        events.append("inner:after")
        return out

    @app.job(name="demo.work")
    async def work() -> None:
        events.append("handler")

    await app.enqueue("demo.work")
    await app.run_worker(run_once=True)
    await app.close()

    assert events == [
        "outer:before",
        "inner:before",
        "handler",
        "inner:after",
        "outer:after",
    ]


@pytest.mark.asyncio
async def test_middleware_does_not_affect_sync_handlers():
    """Sync handlers go through the same chain - the leaf awaits whatever
    the handler returns. Pinning so sync stays a first-class shape."""
    app = Soniq(backend="memory")
    events: List[str] = []

    @app.middleware
    async def logger(ctx: JobContext, call_next):
        events.append("mw")
        return await call_next(ctx)

    @app.job(name="demo.sync")
    def sync_handler() -> None:  # not async on purpose
        events.append("handler")

    await app.enqueue("demo.sync")
    await app.run_worker(run_once=True)
    await app.close()

    assert events == ["mw", "handler"]


@pytest.mark.asyncio
async def test_middleware_exception_lets_processor_record_failure():
    """An exception raised by a middleware (or surfaced through one) is
    treated like any other handler failure: the job retries / dead-letters
    per the configured policy. The middleware itself does not need to
    know about retry mechanics - it just raises."""
    app = Soniq(backend="memory", max_retries=0)
    events: List[str] = []

    @app.middleware
    async def fail_on_demand(ctx: JobContext, call_next):
        if ctx.job_name == "demo.bad":
            raise RuntimeError("middleware says no")
        return await call_next(ctx)

    @app.job(name="demo.bad")
    async def never_runs() -> None:
        events.append("should-not-run")

    job_id = await app.enqueue("demo.bad")
    await app.run_worker(run_once=True)
    # DLQ Option A: dead-lettered jobs are gone from soniq_jobs.
    job = await app.get_job(job_id)
    dlq_row = app.backend._dead_letter_jobs[job_id]
    await app.close()

    # max_retries=0 -> max_attempts=1, dead-letter on first failure.
    assert events == []
    assert job is None
    assert "middleware says no" in dlq_row["last_error"]
