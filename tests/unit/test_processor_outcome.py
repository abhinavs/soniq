"""
How the processor runs a job and records its outcome: a job always leaves
`processing`, JobContext injection, handler timeouts, and the memory backend
handing handlers the same JSON types as Postgres.
"""

import asyncio
import enum
import logging
from typing import Optional

import pytest

from soniq import JobContext, Soniq
from soniq.testing import drain


def _nested(depth):
    root = node = []
    for _ in range(depth):
        child = []
        node.append(child)
        node = child
    return root


class _UnprintableError(Exception):
    def __str__(self):
        raise RuntimeError("str() blew up")


@pytest.mark.asyncio
async def test_deeply_nested_result_finishes_the_job(caplog):
    app = Soniq(backend="memory")

    @app.job(name="outcome.deep")
    async def deep():
        return _nested(200_000)

    job_id = await app.enqueue(deep)
    with caplog.at_level(logging.ERROR):
        await drain(app)

    job = await app.get_job(job_id)
    assert job["status"] == "done"
    assert job["result"] is None
    assert "RecursionError" in caplog.text
    await app.close()


@pytest.mark.asyncio
async def test_result_the_database_rejects_finishes_the_job(caplog):
    # Stands in for Postgres refusing a result over the jsonb size limit.
    app = Soniq(backend="memory")

    @app.job(name="outcome.big")
    async def big():
        return "x" * 100

    await app.ensure_initialized()
    real_mark_done = app.backend.mark_job_done

    async def mark_done(job_id, *, result=None, **kwargs):
        if result is not None:
            raise RuntimeError("string too long to represent as jsonb string")
        return await real_mark_done(job_id, result=result, **kwargs)

    app.backend.mark_job_done = mark_done

    job_id = await app.enqueue(big)
    with caplog.at_level(logging.ERROR):
        await drain(app)

    job = await app.get_job(job_id)
    assert job["status"] == "done"
    assert job["result"] is None
    assert "can't be stored" in caplog.text
    await app.close()


@pytest.mark.asyncio
async def test_exception_whose_str_raises_still_fails_the_job():
    app = Soniq(backend="memory")

    @app.job(name="outcome.unprintable", retries=0)
    async def unprintable():
        raise _UnprintableError()

    job_id = await app.enqueue(unprintable)
    await drain(app)

    job = await app.get_job(job_id)
    assert job["status"] == "dead_letter"
    assert "_UnprintableError: <exception str() failed>" in job["last_error"]
    await app.close()


@pytest.mark.asyncio
async def test_optional_job_context_is_injected():
    app = Soniq(backend="memory")
    seen = []

    @app.job(name="outcome.ctx_optional")
    async def with_optional(n: int, ctx: Optional[JobContext] = None):
        seen.append(ctx)

    @app.job(name="outcome.ctx_union")
    async def with_union(n: int, ctx: "JobContext | None"):
        seen.append(ctx)

    await app.enqueue(with_optional, n=1)
    await app.enqueue(with_union, n=2)
    await drain(app)

    assert len(seen) == 2
    assert all(isinstance(ctx, JobContext) for ctx in seen)
    await app.close()


@pytest.mark.asyncio
async def test_handler_timeout_error_is_not_reported_as_job_timeout():
    app = Soniq(backend="memory")

    @app.job(name="outcome.socket_timeout", retries=0, timeout=5)
    async def socket_timeout():
        raise TimeoutError("socket read timed out")

    @app.job(name="outcome.too_slow", retries=0, timeout=0.05)
    async def too_slow():
        await asyncio.sleep(5)

    own_id = await app.enqueue(socket_timeout)
    slow_id = await app.enqueue(too_slow)
    await drain(app)

    own = (await app.get_job(own_id))["last_error"]
    assert "TimeoutError: socket read timed out" in own
    assert "Job timed out" not in own
    assert "Job timed out after 0.05s" in (await app.get_job(slow_id))["last_error"]
    await app.close()


class _Color(str, enum.Enum):
    RED = "red"


@pytest.mark.asyncio
async def test_memory_backend_hands_handlers_json_types_like_production():
    app = Soniq(backend="memory")
    seen = {}

    @app.job(name="outcome.types")
    async def types(pair: list, color: str, keyed: dict, ctx: JobContext):
        seen.update(pair=pair, color=color, keyed=keyed)

    caller_args = {"pair": (1, 2), "color": _Color.RED, "keyed": {1: "a", None: "b"}}
    job_id = await app.enqueue(types, **caller_args)
    await drain(app)

    assert seen["pair"] == [1, 2]
    assert type(seen["color"]) is str
    assert seen["keyed"] == {"1": "a", "null": "b"}
    # The stored args are a copy, and ctx injection doesn't leak into them.
    assert "ctx" not in (await app.get_job(job_id))["args"]
    assert caller_args["pair"] == (1, 2)
    await app.close()


@pytest.mark.asyncio
async def test_unique_job_with_mixed_dict_keys_dedupes_on_stored_form():
    app = Soniq(backend="memory")

    @app.job(name="outcome.mixed_keys", unique=True)
    async def mixed(data: dict): ...

    first = await app.enqueue(mixed, data={1: "a", "b": 2})
    second = await app.enqueue(mixed, data={"1": "a", "b": 2})
    assert first == second
    await app.close()
