"""``@soniq.job()`` must keep returning the same kind of object as in 0.0.5.

In 0.0.5 the decorator returns a plain ``def`` wrapper built with
``functools.wraps``. It carries ``_soniq_name`` and ``_soniq_config``.
Calling it returns the job's coroutine. User code relies on all of this:
calling the job directly in tests, reading ``__name__`` for logs, and
passing the function to ``enqueue()``.
"""

import inspect
import types

from soniq import Soniq
from soniq.testing import wait_until

# Recorded from soniq 0.0.5. Do not change these to make a test pass.
ISCOROUTINEFUNCTION_0_0_5 = False
CONFIG_KEYS_0_0_5 = {
    "args_model",
    "func",
    "max_retries",
    "priority",
    "queue",
    "retry_backoff",
    "retry_delay",
    "retry_jitter",
    "retry_max_delay",
    "timeout",
    "unique",
}

calls: list = []


async def send_email(to: str, subject: str = "hi") -> dict:
    """Send one email."""
    calls.append((to, subject))
    return {"to": to}


def _decorate(**job_kwargs):
    soniq = Soniq(backend="memory")
    return soniq, soniq.job(**job_kwargs)(send_email)


def test_returns_plain_function_with_same_metadata():
    _, fn = _decorate()
    assert type(fn) is types.FunctionType
    assert inspect.iscoroutinefunction(fn) is ISCOROUTINEFUNCTION_0_0_5
    assert fn.__name__ == "send_email"
    assert fn.__qualname__ == "send_email"
    assert fn.__module__ == __name__
    assert fn.__doc__ == "Send one email."
    assert fn.__wrapped__ is send_email
    assert fn._soniq_name == f"{__name__}.send_email"
    assert set(fn._soniq_config) == CONFIG_KEYS_0_0_5
    assert inspect.signature(fn) == inspect.signature(send_email)


def test_config_keys_with_all_options():
    _, fn = _decorate(
        name="emails.send",
        retries=2,
        priority=5,
        queue="emails",
        unique=True,
        retry_delay=1,
        retry_backoff=True,
        retry_max_delay=10,
        timeout=30,
    )
    assert fn._soniq_name == "emails.send"
    assert set(fn._soniq_config) == CONFIG_KEYS_0_0_5


async def test_direct_await_runs_body():
    _, fn = _decorate()
    calls.clear()
    assert await fn(to="a@example.com") == {"to": "a@example.com"}
    assert await fn("b@example.com", "yo") == {"to": "b@example.com"}
    assert calls == [("a@example.com", "hi"), ("b@example.com", "yo")]


async def test_enqueue_by_function_and_by_name():
    soniq, fn = _decorate(name="emails.send")
    calls.clear()
    try:
        await soniq.enqueue(fn, to="fn@example.com")
        await soniq.enqueue("emails.send", args={"to": "name@example.com"})
        await soniq.run_worker(run_once=True)
        await wait_until(lambda: len(calls) == 2, timeout=5)
    finally:
        await soniq.close()
    assert sorted(calls) == [("fn@example.com", "hi"), ("name@example.com", "hi")]
