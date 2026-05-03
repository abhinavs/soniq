"""
Cross-service integration test.

Two Soniq instances share a single MemoryBackend instance. The producer
has no jobs registered and runs with SONIQ_ENQUEUE_VALIDATION=none.
The consumer registers the task name and runs the worker. The job is
written by one and executed by the other; the test asserts the
plumbing works end-to-end.

This test is verifying the storage and dispatch path, not validation
behavior; strict-mode validation is covered by unit tests in
tests/unit/test_enqueue.py (the strict-mode-registry-table-boundary
load-bearing test in particular).

The MemoryBackend exposes the same StorageBackend protocol the
Postgres backend does, so the test exercises the real cross-service
plumbing without requiring a live database. A Postgres-backed twin
of this test is feasible once the suite-wide DB-state-pollution
issue is resolved separately.
"""

from __future__ import annotations

import os

import pytest

from tests.db_utils import TEST_DATABASE_URL

os.environ.setdefault("SONIQ_DATABASE_URL", TEST_DATABASE_URL)

from soniq import Soniq  # noqa: E402
from soniq.testing.memory_backend import MemoryBackend  # noqa: E402


@pytest.mark.asyncio
async def test_cross_service_enqueue_end_to_end():
    """Producer with no registry sends; consumer with the registered name
    consumes end-to-end."""
    # One backend shared between both Soniq instances - the moral
    # equivalent of a shared Postgres database in production.
    shared_backend = MemoryBackend()

    producer = Soniq(
        backend=shared_backend,
        enqueue_validation="none",
    )

    # Producer registry isolation: as a producer service convention this
    # instance never sees @app.job, so the registry stays empty. This pins
    # the assumption that the test is validating cross-service plumbing
    # rather than an in-process route.
    assert len(producer._job_registry) == 0

    consumer = Soniq(backend=shared_backend)
    received = []

    @consumer.job(name="billing.test.cross_service.v1")
    async def handler(order_id: str, customer: str):
        received.append((order_id, customer))

    job_id = await producer.enqueue(
        "billing.test.cross_service.v1",
        args={"order_id": "o1", "customer": "acme"},
    )

    # Persisted row's job_name must match the explicit name the producer
    # used, not any module-derived form.
    rows = await consumer.list_jobs()
    row = next(r for r in rows if r["id"] == job_id)
    assert row["job_name"] == "billing.test.cross_service.v1"

    # Consumer worker picks up the job and runs the handler.
    await consumer.run_worker(run_once=True)
    assert received == [("o1", "acme")]

    # Job marked done.
    final = await consumer.get_job(job_id)
    assert final["status"] == "done"
