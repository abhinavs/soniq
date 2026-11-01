"""
Tests for soniq.features.webhooks

Covers webhook delivery retry logic, response body size capping, and
delivery queue backpressure. These tests verify that the WebhookDispatcher
handles failures gracefully, avoids unbounded memory usage from large
responses, and enforces queue limits to prevent OOM under load.
"""

import inspect
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("aiohttp")

from soniq.features.webhooks import (  # noqa: E402
    HTTPTransport,
    WebhookDelivery,
    WebhookDispatcher,
    WebhookEndpoint,
    WebhookRegistry,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def dispatcher():
    registry = MagicMock(spec=WebhookRegistry)
    return WebhookDispatcher(registry=registry)


# ---------------------------------------------------------------------------
# Retry behaviour (TEST-01 / FIX-01)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delivery_failure_sets_next_retry_at(dispatcher):
    """When a webhook delivery fails with retries remaining,
    next_retry_at should be set to a future datetime using timedelta."""

    endpoint = MagicMock(spec=WebhookEndpoint)
    endpoint.url = "https://hooks.example.com/test"
    endpoint.plaintext_secret = None
    endpoint.headers = {}
    endpoint.id = "ep-1"
    endpoint.active = True
    endpoint.timeout_seconds = 5

    delivery = MagicMock(spec=WebhookDelivery)
    delivery.id = "del-1"
    delivery.attempts = 0
    delivery.max_attempts = 3
    delivery.status = "pending"
    delivery.endpoint_id = "ep-1"
    delivery.payload = {"event": "job.failed"}
    delivery.event = "job.failed"
    delivery.next_retry_at = None
    delivery.response_status = None
    delivery.response_body = None
    delivery.last_error = None
    delivery.delivered_at = None

    # Make the registry return our endpoint
    dispatcher.registry.get_endpoint = AsyncMock(return_value=endpoint)
    dispatcher._save_delivery_record = AsyncMock()

    # Stub the transport directly; bypassing aiohttp keeps the test focused on
    # retry-backoff bookkeeping and avoids brittle mock plumbing.
    from soniq.features.webhooks import WebhookResult

    dispatcher.transport.deliver = AsyncMock(
        return_value=WebhookResult(
            ok=False, status=500, body="Internal Server Error", error="HTTP 500"
        )
    )

    await dispatcher._process_delivery(delivery)

    # After a failed delivery with retries remaining, next_retry_at should be set
    assert delivery.next_retry_at is not None
    assert isinstance(delivery.next_retry_at, datetime)
    assert delivery.status == "pending"


@pytest.mark.asyncio
async def test_retry_sends_the_same_bytes_whatever_the_key_order(dispatcher):
    """A retry reloads the payload from JSONB, which reorders keys."""
    from soniq.features.webhooks import WebhookResult

    endpoint = MagicMock(spec=WebhookEndpoint, url="https://h.example/x")
    endpoint.plaintext_secret, endpoint.headers, endpoint.active = None, {}, True
    dispatcher.registry.get_endpoint = AsyncMock(return_value=endpoint)
    dispatcher._save_delivery_record = AsyncMock()
    dispatcher.transport.deliver = AsyncMock(
        return_value=WebhookResult(ok=False, status=500, body="", error="HTTP 500")
    )
    bodies = []
    for payload in ({"b": 1, "a": 2}, {"a": 2, "b": 1}):
        delivery = WebhookDelivery(
            id="d", endpoint_id="ep", event="e", payload=payload, status="pending"
        )
        await dispatcher._process_delivery(delivery)
        bodies.append(dispatcher.transport.deliver.call_args.kwargs["payload"])
    assert bodies[0] == bodies[1]


# ---------------------------------------------------------------------------
# Response body size cap (HIGH-04)
# ---------------------------------------------------------------------------


class TestWebhookResponseCap:
    """Verify webhook response body reads are bounded.

    The size cap moved from ``WebhookDispatcher._process_delivery`` into
    the default ``HTTPTransport.deliver`` when delivery became pluggable
    (the dispatcher no longer talks HTTP directly). Custom transports are
    responsible for their own bounds; the default transport must keep
    its 4 KB cap so the in-tree webhook delivery cannot OOM on a chatty
    upstream.
    """

    def test_no_unbounded_response_text(self):
        source = inspect.getsource(HTTPTransport.deliver)
        assert "response.text()" not in source, (
            "Default HTTPTransport must not read the body with unbounded "
            "response.text(); use response.content.read(N) instead."
        )

    def test_response_read_has_size_limit(self):
        source = inspect.getsource(HTTPTransport.deliver)
        assert (
            "content.read(" in source
        ), "Default HTTPTransport must read response body with a size cap"


# ---------------------------------------------------------------------------
# Delivery queue backpressure (MED-05)
# ---------------------------------------------------------------------------


class TestWebhookBackpressure:
    def test_delivery_queue_has_maxsize(self):
        """Webhook delivery queue should have maxsize set."""
        source = inspect.getsource(WebhookDispatcher.__init__)
        assert "maxsize" in source, "delivery_queue must have maxsize for backpressure"
