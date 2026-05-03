"""
Producer service - enqueues by name without owning the handler.

Run after `soniq setup` and after the consumer.py script has registered
the task name. The producer has no @app.job declarations of its own; it
runs with SONIQ_ENQUEUE_VALIDATION=none so the unregistered name does
not raise on the producer side.
"""

from __future__ import annotations

import asyncio
import os

from soniq import Soniq


async def main() -> None:
    db_url = os.environ.get("SONIQ_DATABASE_URL")
    if not db_url:
        raise SystemExit(
            "SONIQ_DATABASE_URL not set. "
            "Try: export SONIQ_DATABASE_URL=postgresql://localhost/soniq_demo"
        )

    producer = Soniq(
        database_url=db_url,
        enqueue_validation="none",
        producer_id="example-producer",
    )

    job_id = await producer.enqueue(
        "billing.invoices.send.v2",
        args={"order_id": "order-42", "customer": "acme"},
    )
    print(f"enqueued job_id={job_id}")
    await producer.close()


if __name__ == "__main__":
    asyncio.run(main())
