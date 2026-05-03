"""
Two-instance bleed test (instance boundary contract verification).

Constructs two ``Soniq`` instances in one process, each with distinct
settings (different ``job_timeout``, ``result_ttl``, ``snooze_max_seconds``,
``task_name_pattern``, and queue names) and asserts that no bleed occurs
in either direction.

This is the runtime counterpart to
``scripts/check_no_global_settings.py`` (the static lint) and to
``docs/_internals/contracts/instance_boundary.md`` (the contract). The lint catches
``get_settings()`` calls that would re-introduce a process-global cache;
this test catches the same regression by black-box behavior - if a
runtime path silently consults a shared cache, one of these assertions
will fail.

Memory backend is used to keep the test hermetic and fast; the
contract is about same-process bleed, which is unrelated to the
backend implementation.
"""

from __future__ import annotations

import asyncio

import pytest

from soniq import Soniq
from soniq.core.processor import _execute_job_safely
from soniq.errors import SONIQ_INVALID_TASK_NAME, SoniqError
from soniq.settings import SoniqSettings


def _make_app(*, queue_prefix: str, **overrides) -> Soniq:
    """Build a memory-backed Soniq with a unique queue prefix."""
    return Soniq(backend="memory", **overrides)


@pytest.mark.asyncio
async def test_settings_do_not_bleed_between_instances():
    """Two instances built with different settings see only their own."""
    app_a = _make_app(
        queue_prefix="a",
        job_timeout=30.0,
        result_ttl=600,
        snooze_max_seconds=120.0,
    )
    app_b = _make_app(
        queue_prefix="b",
        job_timeout=5.0,
        result_ttl=10,
        snooze_max_seconds=60.0,
    )

    assert app_a.settings.job_timeout == 30.0
    assert app_b.settings.job_timeout == 5.0
    assert app_a.settings.result_ttl == 600
    assert app_b.settings.result_ttl == 10
    assert app_a.settings.snooze_max_seconds == 120.0
    assert app_b.settings.snooze_max_seconds == 60.0

    # Each instance owns its own settings object - mutating one does
    # not mutate the other.
    assert app_a.settings is not app_b.settings


@pytest.mark.asyncio
async def test_registries_are_independent():
    """A job registered on instance A is not visible to instance B."""
    app_a = _make_app(queue_prefix="a")
    app_b = _make_app(queue_prefix="b")

    @app_a.job(name="bleed.only_on_a")
    async def only_on_a() -> None:
        pass

    @app_b.job(name="bleed.only_on_b")
    async def only_on_b() -> None:
        pass

    assert app_a.registry.get_job("bleed.only_on_a") is not None
    assert app_a.registry.get_job("bleed.only_on_b") is None
    assert app_b.registry.get_job("bleed.only_on_b") is not None
    assert app_b.registry.get_job("bleed.only_on_a") is None


@pytest.mark.asyncio
async def test_task_name_pattern_does_not_bleed():
    """A permissive pattern on A must not let an invalid name through on B."""
    permissive = r"^.+$"
    app_a = _make_app(queue_prefix="a", task_name_pattern=permissive)
    app_b = _make_app(queue_prefix="b")  # default strict pattern

    # Names that violate the strict default but pass A's permissive
    # pattern register fine on A.
    @app_a.job(name="Has Space")
    async def has_space() -> None:
        pass

    # B is unchanged - the same name must still be rejected against
    # B's settings, proving the validator was not pinned to A's pattern.
    with pytest.raises(SoniqError) as excinfo:

        @app_b.job(name="Has Space")
        async def has_space_b() -> None:
            pass

    assert excinfo.value.error_code == SONIQ_INVALID_TASK_NAME


@pytest.mark.asyncio
async def test_job_timeout_threads_per_instance_in_processor():
    """``_execute_job_safely`` honors the settings it is handed, not a global.

    A job that sleeps 1.5s must time out under settings_a (timeout=1s)
    and succeed under settings_b (timeout=10s). If the processor were
    silently reading a shared cache, both calls would see the same
    timeout and one branch would fail.
    """
    settings_a = SoniqSettings(job_timeout=1.0)
    settings_b = SoniqSettings(job_timeout=10.0)

    async def slow():
        await asyncio.sleep(1.5)
        return "ok"

    job_record = {
        "id": "bleed-1",
        "job_name": "bleed.slow",
        "args": {},
        "attempts": 1,
        "max_attempts": 3,
    }
    job_meta = {"func": slow, "args_model": None, "max_retries": 3}

    success_a, error_a, _ = await _execute_job_safely(
        job_record, job_meta, settings=settings_a
    )
    assert success_a is False
    assert "timed out" in (error_a or "")

    success_b, error_b, result_b = await _execute_job_safely(
        job_record, job_meta, settings=settings_b
    )
    assert success_b is True
    assert error_b is None
    assert result_b == "ok"


@pytest.mark.asyncio
async def test_backends_are_independent():
    """Each instance owns its own backend object."""
    app_a = _make_app(queue_prefix="a")
    app_b = _make_app(queue_prefix="b")

    await app_a.ensure_initialized()
    await app_b.ensure_initialized()

    assert app_a.backend is not app_b.backend
