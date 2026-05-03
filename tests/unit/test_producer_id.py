"""
Tests for the producer_id resolver and the per-row stamp on enqueue.

'Who enqueued this poison message?' is the first question oncall asks
once queues cross repo boundaries. Each enqueued row carries the
producer_id of the instance that wrote it.
"""

from __future__ import annotations

import os

import pytest

from tests.db_utils import TEST_DATABASE_URL

os.environ.setdefault("SONIQ_DATABASE_URL", TEST_DATABASE_URL)

from soniq import Soniq  # noqa: E402
from soniq.testing import make_app  # noqa: E402
from soniq.utils.producer_id import (  # noqa: E402
    _reset_cache_for_tests,
    resolve_producer_id,
)


@pytest.fixture(autouse=True)
def _reset_producer_cache():
    _reset_cache_for_tests()
    yield
    _reset_cache_for_tests()


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


def test_explicit_value_returned_verbatim():
    assert resolve_producer_id("billing-api") == "billing-api"


def test_auto_resolves_to_host_pid_argv0_shape():
    """The 'auto' sentinel composes <host>:<pid>:<argv0>. We don't pin the
    exact string (depends on the runtime); we pin the shape."""
    value = resolve_producer_id("auto")
    parts = value.split(":")
    assert len(parts) == 3
    host, pid, argv0 = parts
    assert host  # non-empty
    assert pid.isdigit()
    assert argv0  # non-empty


def test_auto_value_is_cached_per_process():
    a = resolve_producer_id("auto")
    b = resolve_producer_id("auto")
    assert a == b
    assert a is b  # same string object - the cache hit returned it directly


def test_explicit_value_is_not_cached_across_calls_with_different_strings():
    a = resolve_producer_id("first")
    b = resolve_producer_id("second")
    assert a == "first" and b == "second"


# ---------------------------------------------------------------------------
# Setting default + override
# ---------------------------------------------------------------------------


def test_setting_defaults_to_auto_sentinel():
    """`SONIQ_PRODUCER_ID` defaults to 'auto'; the resolver expands it."""
    app = make_app()
    assert app.settings.producer_id == "auto"


def test_setting_can_be_overridden_via_constructor():
    app = make_app(producer_id="billing-api")
    assert app.settings.producer_id == "billing-api"


# ---------------------------------------------------------------------------
# Per-row stamp on enqueue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_row_carries_producer_id_after_enqueue():
    """Auto-resolved value is stamped on every row this instance enqueues."""
    app = make_app(enqueue_validation="none")
    job_id = await app.enqueue("billing.who", args={})
    rows = await app.list_jobs()
    row = next(r for r in rows if r["id"] == job_id)
    assert "producer_id" in row
    assert isinstance(row["producer_id"], str)
    assert ":" in row["producer_id"]  # auto shape


@pytest.mark.asyncio
async def test_explicit_producer_id_stamped_verbatim():
    app = make_app(enqueue_validation="none", producer_id="billing-api")
    job_id = await app.enqueue("billing.who2", args={})
    rows = await app.list_jobs()
    row = next(r for r in rows if r["id"] == job_id)
    assert row["producer_id"] == "billing-api"


@pytest.mark.asyncio
async def test_distinct_instances_can_stamp_different_ids():
    """Two Soniq instances with different producer_ids stamp distinctly."""
    from soniq.testing.memory_backend import MemoryBackend

    backend = MemoryBackend()
    a = Soniq(backend=backend, producer_id="service-a", enqueue_validation="none")
    b = Soniq(backend=backend, producer_id="service-b", enqueue_validation="none")

    id_a = await a.enqueue("billing.from_a", args={})
    id_b = await b.enqueue("billing.from_b", args={})

    rows = await a.list_jobs()
    row_a = next(r for r in rows if r["id"] == id_a)
    row_b = next(r for r in rows if r["id"] == id_b)
    assert row_a["producer_id"] == "service-a"
    assert row_b["producer_id"] == "service-b"
