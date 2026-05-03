"""
WebhookDispatcher.dispatch_event must apply backpressure: a full delivery
queue causes the excess deliveries to be dropped (recoverable via the DB
retry processor) and emit a single WARN log per dispatch with the count.
"""

import logging

import pytest


@pytest.mark.asyncio
async def test_full_delivery_queue_drops_and_warns(caplog):
    from soniq.features.webhooks import (
        WebhookDispatcher,
        WebhookEndpoint,
        WebhookEvent,
        WebhookRegistry,
    )

    # Two registered endpoints, queue size 1 -> second delivery drops.
    from soniq.testing.helpers import make_app

    registry = WebhookRegistry(make_app())
    registry.endpoints = {
        "a": WebhookEndpoint(id="a", url="http://a/"),
        "b": WebhookEndpoint(id="b", url="http://b/"),
    }
    dispatcher = WebhookDispatcher(registry, max_concurrent_deliveries=2)
    # Replace the default 1000-deep queue with a tiny one for the test.
    import asyncio

    dispatcher.delivery_queue = asyncio.Queue(maxsize=1)

    caplog.set_level(logging.WARNING, logger="soniq.features.webhooks")
    await dispatcher.dispatch_event(WebhookEvent.JOB_COMPLETED, {"job": "x"})

    # One warning emitted with the drop count.
    drop_warnings = [
        r for r in caplog.records if "delivery queue full" in r.getMessage()
    ]
    assert len(drop_warnings) == 1
    assert "dropped 1/2" in drop_warnings[0].getMessage()
