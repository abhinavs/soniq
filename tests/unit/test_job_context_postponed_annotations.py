"""JobContext injection when annotations are strings.

``from __future__ import annotations`` turns ``ctx: JobContext`` into the
string ``"JobContext"``. Enqueue and the worker must still recognise it.
"""

from __future__ import annotations

import pytest

from soniq import JobContext, Soniq
from soniq.job import context_param
from soniq.testing import drain


def test_context_param_resolves_string_annotations():
    async def job(order_id: int, ctx: JobContext): ...

    # Not importable here, like an import kept under TYPE_CHECKING.
    async def unresolved(order_id: int, ctx: models.JobContext): ...  # noqa: F821

    async def plain(order_id: int): ...

    assert context_param(job) == "ctx"
    assert context_param(unresolved) == "ctx"
    assert context_param(plain) is None


@pytest.mark.asyncio
async def test_enqueue_and_run_with_postponed_annotations():
    app = Soniq(backend="memory")

    @app.job()
    async def process_order(order_id: int, ctx: JobContext):
        return {"order": order_id, "attempt": ctx.attempt}

    await app.enqueue(process_order, order_id=1)
    assert await drain(app) == [{"order": 1, "attempt": 1}]
