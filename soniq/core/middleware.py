"""
Middleware Protocol and chain composition.

A middleware wraps a handler call. It receives the ``JobContext`` and a
``call_next`` callable that, when awaited, runs the rest of the chain
(eventually the handler). This shape is the same one Starlette / Django
use - users already understand it.

Hooks (``before_job`` / ``after_job`` / ``on_error``) stay for one-shot
side effects without wrap-around control. Reach for middleware when you
need to set up state that the handler should see (a tracing span, a
ContextVar, a database transaction) and tear it down after the handler
returns or raises.
"""

from typing import Any, Awaitable, Callable, List, Protocol, runtime_checkable

from soniq.job import JobContext

# A handler-like callable: takes a JobContext, returns the awaited handler
# return value. Both the inner handler shim and ``call_next`` share this
# shape so middleware can compose arbitrarily.
JobHandler = Callable[[JobContext], Awaitable[Any]]


@runtime_checkable
class Middleware(Protocol):
    """Async callable that wraps a job handler.

    Implementations call ``await call_next(ctx)`` to continue the chain
    and return its value (or transform it). Raising propagates outward
    through the rest of the chain like a normal exception.
    """

    async def __call__(self, ctx: JobContext, call_next: JobHandler) -> Any: ...


def build_chain(
    middleware: List[Middleware],
    handler: JobHandler,
) -> JobHandler:
    """Compose ``middleware`` around ``handler`` into a single callable.

    Order semantics: the first item in ``middleware`` is the outermost
    wrapper, the last item is closest to the handler. Calling the
    returned callable runs through them in that order.

    With an empty list, ``handler`` is returned unchanged so the no-
    middleware path stays as cheap as a direct call.
    """
    chain: JobHandler = handler
    for mw in reversed(middleware):
        chain = _wrap(mw, chain)
    return chain


def _wrap(mw: Middleware, nxt: JobHandler) -> JobHandler:
    async def wrapped(ctx: JobContext) -> Any:
        return await mw(ctx, nxt)

    return wrapped
