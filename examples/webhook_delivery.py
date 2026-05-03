"""Webhook delivery example.

Start a local webhook receiver (any HTTP server) at
``http://localhost:8080/webhook`` and then run this script.
"""

import asyncio
import os

from soniq import Soniq
from soniq.features.webhooks import WebhookEvent


async def main() -> None:
    app = Soniq(
        database_url=os.environ.get(
            "SONIQ_DATABASE_URL", "postgresql://localhost/myapp"
        )
    )
    await app.setup()

    await app.webhooks.start()
    try:
        await app.webhooks.register(
            url="http://localhost:8080/webhook",
            events=[WebhookEvent.JOB_COMPLETED.value],
        )

        await app.webhooks.send_webhook(
            WebhookEvent.JOB_COMPLETED,
            data={
                "job_id": "job-123",
                "job_name": "examples.webhook_delivery",
                "queue": "default",
                "duration_ms": 10.0,
            },
        )
    finally:
        await app.webhooks.stop()
        await app.close()


if __name__ == "__main__":
    asyncio.run(main())
