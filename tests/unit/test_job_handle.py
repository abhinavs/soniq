"""``job.enqueue(...)`` and ``job.options(...)`` on decorated functions."""

import functools
import logging
import types
from datetime import timedelta

import pytest

from soniq import Soniq


@pytest.fixture
async def soniq():
    instance = Soniq(backend="memory")
    yield instance
    await instance.close()


async def test_decorator_still_returns_plain_function(soniq):
    async def send(to: str) -> None: ...

    fn = soniq.job()(send)
    assert type(fn) is types.FunctionType
    assert fn.__wrapped__ is send
    assert callable(fn.enqueue) and callable(fn.options)
    assert "enqueue" not in fn._soniq_config


async def test_enqueue_matches_soniq_enqueue(soniq):
    @soniq.job(name="emails.send", queue="emails", priority=7)
    async def send(to: str) -> None: ...

    job_id = await send.enqueue(to="a@example.com")
    job = await soniq.get_job(job_id)
    assert job["job_name"] == "emails.send"
    assert job["queue"] == "emails"
    assert job["priority"] == 7
    assert job["args"] == {"to": "a@example.com"}


async def test_options_override_enqueue_options(soniq):
    @soniq.job(name="emails.send")
    async def send(to: str) -> None: ...

    job_id = await send.options(queue="urgent", priority=1).enqueue(to="b@x.io")
    job = await soniq.get_job(job_id)
    assert job["queue"] == "urgent"
    assert job["priority"] == 1
    assert job["args"] == {"to": "b@x.io"}


async def test_options_dedup_key_returns_same_job(soniq):
    @soniq.job()
    async def send(to: str) -> None: ...

    handle = send.options(dedup_key="once", scheduled_at=timedelta(seconds=60))
    first = await handle.enqueue(to="c@x.io")
    second = await handle.enqueue(to="c@x.io")
    assert first == second


async def test_enqueue_routes_to_registering_instance():
    a, b = Soniq(backend="memory"), Soniq(backend="memory")

    @a.job(name="jobs.a")
    async def job_a() -> None: ...

    try:
        job_id = await job_a.enqueue()
        assert await a.get_job(job_id) is not None
        assert await b.list_jobs() == []
    finally:
        await a.close()
        await b.close()


async def test_positional_args_raise(soniq):
    @soniq.job()
    async def send(to: str) -> None: ...

    with pytest.raises(TypeError, match="keywords only"):
        await send.enqueue("a@example.com")


async def test_direct_call_and_outer_decorator_still_work(soniq):
    @soniq.job()
    async def add(x: int, y: int) -> int:
        return x + y

    def logged(fn):
        @functools.wraps(fn)
        async def inner(**kwargs):
            return await fn(**kwargs)

        return inner

    assert await add(x=1, y=2) == 3
    assert await logged(add)(x=2, y=2) == 4
    # functools.wraps copies the helpers onto the outer wrapper too.
    job_id = await logged(add).enqueue(x=1, y=1)
    assert (await soniq.get_job(job_id))["args"] == {"x": 1, "y": 1}


async def test_user_attribute_is_not_replaced(soniq, caplog):
    async def send(to: str) -> None: ...

    def own_enqueue():
        return "mine"

    send.enqueue = own_enqueue
    with caplog.at_level(logging.DEBUG, logger="soniq.core.registry"):
        fn = soniq.job()(send)
    assert fn.enqueue is own_enqueue
    assert callable(fn.options)
    assert "already has attribute 'enqueue'" in caplog.text
