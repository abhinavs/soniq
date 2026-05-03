"""
Tests for SONIQ_ROUTE_MAP consumer-side prefix routing.

route_map is consumer-side only. The producer is unaware of it;
their explicit queue= lands on the row. The map only affects the
queue the consumer's worker polls when a @app.job is registered
without an explicit queue=.

Precedence chain:
    consumer-side at registration:
        explicit @app.job(queue=...) > route_map prefix match > "default"
    producer-side at enqueue:
        explicit queue= > ref.default_queue > "default"

When producer override and consumer route_map disagree, the row's
queue is whatever the producer wrote. The consumer worker only sees
the row if it polls that queue.
"""

from __future__ import annotations

import os

import pytest

from tests.db_utils import TEST_DATABASE_URL

os.environ.setdefault("SONIQ_DATABASE_URL", TEST_DATABASE_URL)

from soniq.testing import make_app  # noqa: E402

# ---------------------------------------------------------------------------
# Consumer-side: route_map at registration
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,expected_queue",
    [
        ("billing.invoices.send", "billing-queue"),
        ("reports.daily.run", "reports-queue"),
        ("misc.ping", "default"),  # no prefix match -> system default
    ],
)
def test_route_map_applied_when_no_explicit_queue(name, expected_queue):
    app = make_app(
        route_map={"billing.": "billing-queue", "reports.": "reports-queue"},
    )

    @app.job(name=name)
    async def handler():
        pass

    meta = app._job_registry.get_job(name)
    assert meta["queue"] == expected_queue


def test_explicit_queue_overrides_route_map():
    """`@app.job(queue=...)` always wins over a prefix match."""
    app = make_app(route_map={"billing.": "billing-queue"})

    @app.job(name="billing.foo", queue="urgent")
    async def handler():
        pass

    meta = app._job_registry.get_job("billing.foo")
    assert meta["queue"] == "urgent"


def test_longest_prefix_wins():
    """Multiple prefixes match -> the more specific one wins."""
    app = make_app(
        route_map={
            "billing.": "billing-queue",
            "billing.invoices.": "invoices-queue",
        },
    )

    @app.job(name="billing.invoices.send")
    async def handler():
        pass

    meta = app._job_registry.get_job("billing.invoices.send")
    assert meta["queue"] == "invoices-queue"


def test_route_map_empty_falls_back_to_system_default():
    app = make_app(route_map={})

    @app.job(name="anything.foo")
    async def handler():
        pass

    meta = app._job_registry.get_job("anything.foo")
    assert meta["queue"] == "default"


# ---------------------------------------------------------------------------
# Producer-side disagreement: row queue is what the producer wrote
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_producer_override_disagrees_with_consumer_route_map():
    """The producer passes queue='urgent'; the consumer route_map maps
    the name to 'billing'. The row's queue column is 'urgent' (no
    write-time rewrite). A worker polling 'urgent' consumes it; a
    worker polling only 'billing' does not."""
    app = make_app(route_map={"billing.": "billing-queue"}, enqueue_validation="none")

    @app.job(name="billing.disagree")
    async def handler():
        pass

    job_id = await app.enqueue("billing.disagree", args={}, queue="urgent")
    rows = await app.list_jobs()
    row = next(r for r in rows if r["id"] == job_id)
    # No write-time rewrite: row queue is what the producer wrote.
    assert row["queue"] == "urgent"
    # The handler is registered on 'billing-queue' (the route_map maps to it).
    meta = app._job_registry.get_job("billing.disagree")
    assert meta["queue"] == "billing-queue"


@pytest.mark.asyncio
async def test_producer_omits_queue_with_route_map_uses_registered_queue():
    """When the producer omits queue= and the consumer's @app.job has the
    name registered with route_map applied, the registered queue
    becomes the row's queue (because the registered job_meta is
    consulted on the producer side too).

    The producer-omits-no-registration case is documented separately:
    a true cross-service producer with no local registry sees system
    default 'default' (covered by the TaskRef.default_queue test in
    test_enqueue.py)."""
    app = make_app(route_map={"billing.": "billing-queue"}, enqueue_validation="strict")

    @app.job(name="billing.omit_q")
    async def handler():
        pass

    job_id = await app.enqueue("billing.omit_q", args={})
    rows = await app.list_jobs()
    row = next(r for r in rows if r["id"] == job_id)
    assert row["queue"] == "billing-queue"


@pytest.mark.asyncio
async def test_producer_omits_queue_no_local_registration_uses_default():
    """The producer-omits-queue limitation: a true cross-service
    producer with no local registration of the name and
    no TaskRef.default_queue lands the row on 'default', not on
    whatever the consumer's route_map would map the name to. The
    producer is unaware of the consumer's routing, so a 'billing'-only
    worker would miss this row."""
    app = make_app(route_map={"billing.": "billing-queue"}, enqueue_validation="none")
    # No @app.job for this name on the producer side.

    job_id = await app.enqueue("billing.no_local_reg", args={})
    rows = await app.list_jobs()
    row = next(r for r in rows if r["id"] == job_id)
    # Strongest argument for 'omit queue= cross-service' in the docs.
    assert row["queue"] == "default"
