import asyncio

import pytest

pytest.importorskip("aiohttp")
from aiohttp import web  # noqa: E402

from soniq import Soniq  # noqa: E402
from soniq.features.webhooks import WebhookEvent  # noqa: E402
from tests.db_utils import TEST_DATABASE_URL  # noqa: E402


@pytest.mark.asyncio
async def test_webhook_delivery_smoke():
    received = []
    delivered = asyncio.Event()

    async def handler(request):
        payload = await request.json()
        received.append(payload)
        delivered.set()
        return web.Response(text="ok")

    aiohttp_app = web.Application()
    aiohttp_app.router.add_post("/webhook", handler)
    runner = web.AppRunner(aiohttp_app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()

    port = site._server.sockets[0].getsockname()[1]
    url = f"http://127.0.0.1:{port}/webhook"

    soniq_app = Soniq(database_url=TEST_DATABASE_URL)
    await soniq_app._ensure_initialized()

    try:
        async with soniq_app.backend._pool.acquire() as conn:
            await conn.execute(
                "TRUNCATE TABLE soniq_webhook_deliveries, soniq_webhook_endpoints RESTART IDENTITY CASCADE"
            )

        webhooks = soniq_app.webhooks
        await webhooks.start()
        await webhooks.register(url)

        await webhooks.send_webhook(
            WebhookEvent.JOB_COMPLETED,
            {
                "job_id": "job-123",
                "job_name": "tests.webhook_job",
                "queue": "default",
                "duration_ms": 12.5,
            },
        )

        await asyncio.wait_for(delivered.wait(), timeout=5)
        assert received
        assert received[0]["event"] == "job.completed"
        assert received[0]["data"]["job_id"] == "job-123"
    finally:
        await soniq_app.webhooks.stop()
        await soniq_app.close()
        await runner.cleanup()
