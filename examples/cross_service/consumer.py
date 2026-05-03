"""
Consumer service - registers the handler under the same task name the
producer uses.

In production you start the worker via the CLI:

    export SONIQ_DATABASE_URL="postgresql://shared-pg/jobs"
    export SONIQ_JOBS_MODULES="examples.cross_service.consumer"
    soniq worker

This script can also be run directly as a quick smoke check; the
in-process `app.run_worker()` call at the bottom is the embedded /
test-time entry point, not the recommended deploy path.
"""

from __future__ import annotations

import asyncio
import logging
import os

from pydantic import BaseModel

from soniq import Soniq

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")


class InvoiceArgs(BaseModel):
    order_id: str
    customer: str


async def main() -> None:
    db_url = os.environ.get("SONIQ_DATABASE_URL")
    if not db_url:
        raise SystemExit(
            "SONIQ_DATABASE_URL not set. "
            "Try: export SONIQ_DATABASE_URL=postgresql://localhost/soniq_demo"
        )

    consumer = Soniq(database_url=db_url)

    @consumer.job(name="billing.invoices.send.v2", validate=InvoiceArgs)
    async def send_invoice(order_id: str, customer: str) -> None:
        logging.info("handler ran: order_id=%s customer=%s", order_id, customer)

    logging.info("consumer waiting for jobs (Ctrl-C to stop)...")
    await consumer.run_worker()


if __name__ == "__main__":
    asyncio.run(main())
