"""
Tests for webhooks.py data structures.

Covers: WebhookEndpoint post_init, WebhookDelivery post_init,
WebhookPayload, WebhookEvent enum.
"""

import os
from datetime import datetime, timezone

import pytest

pytest.importorskip("aiohttp")

os.environ.setdefault("SONIQ_WEBHOOKS_ENABLED", "true")

from soniq.features.webhooks import (  # noqa: E402
    WebhookDelivery,
    WebhookEndpoint,
    WebhookEvent,
    WebhookPayload,
)


class TestWebhookEvent:
    def test_event_values(self):
        assert WebhookEvent.JOB_QUEUED == "job.queued"
        assert WebhookEvent.JOB_STARTED == "job.started"
        assert WebhookEvent.JOB_COMPLETED == "job.completed"
        assert WebhookEvent.JOB_FAILED == "job.failed"
        assert WebhookEvent.JOB_DEAD_LETTER == "job.dead_letter"


class TestWebhookPayload:
    def test_to_dict(self):
        payload = WebhookPayload(
            event="job.completed",
            timestamp=datetime.now(timezone.utc).isoformat(),
            data={"result": "ok"},
        )
        d = payload.to_dict()
        assert d["event"] == "job.completed"
        assert d["data"]["result"] == "ok"


class TestWebhookEndpoint:
    def test_post_init_sets_default_events(self):
        ep = WebhookEndpoint(
            id="ep-1",
            url="https://example.com/hook",
        )
        assert ep.events is not None
        assert len(ep.events) > 0
        assert ep.active is True

    def test_post_init_encrypts_secret(self):
        pytest.importorskip("cryptography")
        ep = WebhookEndpoint(
            id="ep-2",
            url="https://example.com/hook",
            secret="my-webhook-secret",
        )
        # Secret should be encrypted by post_init
        assert ep.secret != "my-webhook-secret" or ep._secure_secret is not None

    def test_custom_headers(self):
        ep = WebhookEndpoint(
            id="ep-3",
            url="https://example.com/hook",
            headers={"Authorization": "Bearer token"},
        )
        assert ep.headers["Authorization"] == "Bearer token"


class TestWebhookDelivery:
    def test_post_init_sets_created_at(self):
        delivery = WebhookDelivery(
            id="del-1",
            endpoint_id="ep-1",
            event="job.completed",
            payload={"event": "job.completed"},
            status="pending",
        )
        assert delivery.created_at is not None
        assert delivery.attempts == 0

    def test_delivery_with_max_attempts(self):
        delivery = WebhookDelivery(
            id="del-2",
            endpoint_id="ep-1",
            event="job.failed",
            payload={"event": "job.failed"},
            status="pending",
            max_attempts=5,
        )
        assert delivery.max_attempts == 5
