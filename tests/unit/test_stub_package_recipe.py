"""
End-to-end smoke test for the cross-service task-stubs recipe.

The recipe (docs/recipes/cross-service-task-stubs.md) shows how a
producer and a consumer share a small stub package containing
TaskRef declarations. This test inlines the moral equivalent of
that package and exercises it against a shared MemoryBackend.

The point is to pin the recipe's contract: the stub-package values
are TaskRefs, the producer enqueues against them, and the consumer
registers a handler under the same canonical name and runs it.
"""

from __future__ import annotations

import os

import pytest
from pydantic import BaseModel

from tests.db_utils import TEST_DATABASE_URL

os.environ.setdefault("SONIQ_DATABASE_URL", TEST_DATABASE_URL)

from soniq import Soniq, task_ref  # noqa: E402
from soniq.testing.memory_backend import MemoryBackend  # noqa: E402

# --- the "stub package" -----------------------------------------------------
# In a real deployment this lives in `myservice_tasks/`; here we inline it
# to keep the smoke test self-contained.


class InvoiceArgs(BaseModel):
    order_id: str
    customer: str


send_invoice = task_ref(
    name="billing.invoices.send.v2",
    args_model=InvoiceArgs,
    default_queue="billing",
)


# --- the test ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_stub_package_recipe_end_to_end():
    backend = MemoryBackend()

    producer = Soniq(backend=backend)
    consumer = Soniq(backend=backend)

    received = []

    @consumer.job(name=send_invoice.name, validate=InvoiceArgs)
    async def handler(order_id: str, customer: str):
        received.append((order_id, customer))

    # Producer enqueues against the imported TaskRef. The ref carries
    # the canonical name, the args_model, and a default_queue - the
    # producer does not need any other consumer-side knowledge.
    job_id = await producer.enqueue(
        send_invoice,
        args={"order_id": "o1", "customer": "acme"},
    )

    # The row was written under the explicit canonical name and on the
    # ref's default_queue.
    rows = await consumer.list_jobs()
    row = next(r for r in rows if r["id"] == job_id)
    assert row["job_name"] == "billing.invoices.send.v2"
    assert row["queue"] == "billing"

    # Consumer worker on the billing queue picks it up and runs the
    # handler with the producer-supplied args.
    await consumer.run_worker(run_once=True, queues=["billing"])
    assert received == [("o1", "acme")]


@pytest.mark.asyncio
async def test_stub_package_recipe_args_model_protects_producer():
    """A producer typo (int instead of str) is caught at the producer
    side via the TaskRef's args_model, before any row is written."""
    from soniq.errors import SONIQ_TASK_ARGS_INVALID, SoniqError

    producer = Soniq(backend=MemoryBackend())
    with pytest.raises(SoniqError) as exc_info:
        await producer.enqueue(send_invoice, args={"order_id": 123, "customer": "acme"})
    assert exc_info.value.error_code == SONIQ_TASK_ARGS_INVALID
