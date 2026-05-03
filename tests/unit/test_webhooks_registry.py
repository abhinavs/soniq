"""
Tests for webhooks.py — WebhookSigner and WebhookRegistry in-memory operations.
"""

import os
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("aiohttp")

os.environ.setdefault("SONIQ_WEBHOOKS_ENABLED", "true")

from soniq.features.webhooks import (  # noqa: E402
    WebhookEndpoint,
    WebhookEvent,
    WebhookRegistry,
    WebhookSigner,
)
from soniq.testing.helpers import make_app  # noqa: E402


class TestWebhookSigner:
    def test_sign_payload(self):
        signature = WebhookSigner.sign_payload("test payload", "secret")
        assert signature.startswith("sha256=")
        assert len(signature) > 10

    def test_verify_valid_signature(self):
        payload = '{"event": "job.completed"}'
        secret = "my-secret"
        signature = WebhookSigner.sign_payload(payload, secret)
        assert WebhookSigner.verify_signature(payload, signature, secret) is True

    def test_verify_invalid_signature(self):
        assert (
            WebhookSigner.verify_signature("payload", "sha256=wrong", "secret") is False
        )

    def test_different_payloads_different_signatures(self):
        sig1 = WebhookSigner.sign_payload("payload1", "secret")
        sig2 = WebhookSigner.sign_payload("payload2", "secret")
        assert sig1 != sig2

    def test_different_secrets_different_signatures(self):
        sig1 = WebhookSigner.sign_payload("payload", "secret1")
        sig2 = WebhookSigner.sign_payload("payload", "secret2")
        assert sig1 != sig2


class TestWebhookRegistry:
    @pytest.mark.asyncio
    async def test_register_and_get_endpoint(self):
        registry = WebhookRegistry(make_app())
        ep = WebhookEndpoint(id="ep-1", url="https://example.com/hook")

        with patch.object(registry, "_save_endpoint_to_db", new_callable=AsyncMock):
            eid = await registry.register_endpoint(ep)

        assert eid == "ep-1"
        result = await registry.get_endpoint("ep-1")
        assert result is ep

    @pytest.mark.asyncio
    async def test_unregister_endpoint(self):
        registry = WebhookRegistry(make_app())
        ep = WebhookEndpoint(id="ep-1", url="https://example.com/hook")

        with patch.object(registry, "_save_endpoint_to_db", new_callable=AsyncMock):
            await registry.register_endpoint(ep)

        with patch.object(registry, "_delete_endpoint_from_db", new_callable=AsyncMock):
            result = await registry.unregister_endpoint("ep-1")

        assert result is True
        assert await registry.get_endpoint("ep-1") is None

    @pytest.mark.asyncio
    async def test_unregister_missing_returns_false(self):
        registry = WebhookRegistry(make_app())
        result = await registry.unregister_endpoint("nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_list_endpoints(self):
        registry = WebhookRegistry(make_app())
        ep1 = WebhookEndpoint(id="ep-1", url="https://a.com")
        ep2 = WebhookEndpoint(id="ep-2", url="https://b.com")

        with patch.object(registry, "_save_endpoint_to_db", new_callable=AsyncMock):
            await registry.register_endpoint(ep1)
            await registry.register_endpoint(ep2)

        endpoints = await registry.list_endpoints()
        assert len(endpoints) == 2

    @pytest.mark.asyncio
    async def test_get_endpoints_for_event(self):
        registry = WebhookRegistry(make_app())
        ep1 = WebhookEndpoint(
            id="ep-1",
            url="https://a.com",
            events=["job.completed"],
        )
        ep2 = WebhookEndpoint(
            id="ep-2",
            url="https://b.com",
            events=["job.failed"],
        )
        ep3 = WebhookEndpoint(
            id="ep-3",
            url="https://c.com",
            events=None,  # subscribes to all events
        )

        with patch.object(registry, "_save_endpoint_to_db", new_callable=AsyncMock):
            await registry.register_endpoint(ep1)
            await registry.register_endpoint(ep2)
            await registry.register_endpoint(ep3)

        completed_eps = await registry.get_endpoints_for_event(
            WebhookEvent.JOB_COMPLETED
        )
        assert len(completed_eps) == 2  # ep1 and ep3

        failed_eps = await registry.get_endpoints_for_event(WebhookEvent.JOB_FAILED)
        assert len(failed_eps) == 2  # ep2 and ep3

    @pytest.mark.asyncio
    async def test_inactive_endpoint_excluded_from_event_filter(self):
        registry = WebhookRegistry(make_app())
        ep = WebhookEndpoint(
            id="ep-1",
            url="https://a.com",
            events=["job.completed"],
            active=False,
        )

        with patch.object(registry, "_save_endpoint_to_db", new_callable=AsyncMock):
            await registry.register_endpoint(ep)

        result = await registry.get_endpoints_for_event(WebhookEvent.JOB_COMPLETED)
        assert len(result) == 0
