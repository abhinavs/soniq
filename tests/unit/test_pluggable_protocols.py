"""
Soniq exposes two pluggable extension points in 0.0.2:

- `RetryPolicy` (`soniq.core.retry`)
- `MetricsSink` (`soniq.observability`)

Each ships a default implementation and a `Soniq(...)` constructor
parameter. These tests pin the contract.
"""

import pytest

from soniq import Soniq
from soniq.core.processor import process_job_via_backend
from soniq.core.registry import JobRegistry
from soniq.core.retry import (
    DEFAULT_RETRY_POLICY,
    ExponentialBackoff,
    RetryPolicy,
)
from soniq.testing.memory_backend import MemoryBackend


def test_default_retry_policy_implements_protocol():
    assert isinstance(DEFAULT_RETRY_POLICY, RetryPolicy)
    assert isinstance(ExponentialBackoff(), RetryPolicy)


def test_soniq_accepts_custom_retry_policy():
    class _Policy:
        def delay_for(self, *, attempt, job_meta, exc):
            return 0.1

    app = Soniq(
        database_url="postgresql://user:pass@localhost/test", retry_policy=_Policy()
    )
    assert app._retry_policy.delay_for(attempt=1, job_meta={}, exc=Exception()) == 0.1


def test_soniq_no_log_sink_attr():
    """``log_sink`` was a half-wired knob and was removed in 0.0.2."""
    app = Soniq(database_url="postgresql://user:pass@localhost/test")
    assert not hasattr(app, "log_sink")
    assert not hasattr(app, "_log_sink")


def test_soniq_no_serializer_attr():
    """The half-wired ``serializer`` knob was removed in 0.0.2. The
    constructor swallows unknown kwargs through
    ``SoniqSettings(extra='ignore')``, so pin removal at the
    attribute / property level instead."""
    app = Soniq(database_url="postgresql://user:pass@localhost/test")
    assert not hasattr(app, "serializer")
    assert not hasattr(app, "_serializer")


@pytest.mark.asyncio
async def test_get_result_with_pydantic_result_model():
    """`get_result(..., result_model=Model)` validates the stored dict
    through `model_validate` and returns the constructed instance."""
    from pydantic import BaseModel

    class JobResult(BaseModel):
        ok: bool
        message: str

    app = Soniq(backend="memory")
    await app._ensure_initialized()

    @app.job(name="returns_dict")
    async def returns_dict():
        return {"ok": True, "message": "hi"}

    job_id = await app.enqueue("returns_dict")
    await app.run_worker(run_once=True)

    typed = await app.get_result(job_id, result_model=JobResult)
    assert isinstance(typed, JobResult)
    assert typed.ok is True
    assert typed.message == "hi"

    raw = await app.get_result(job_id)
    assert raw == {"ok": True, "message": "hi"}

    await app.close()


@pytest.mark.asyncio
async def test_get_result_with_dataclass_result_model():
    """A non-Pydantic class is constructed via `**dict` when the stored
    value is a dict, mirroring the normal dataclass call."""
    from dataclasses import dataclass

    @dataclass
    class Pair:
        a: int
        b: int

    app = Soniq(backend="memory")
    await app._ensure_initialized()

    @app.job(name="returns_pair")
    async def returns_pair():
        return {"a": 1, "b": 2}

    job_id = await app.enqueue("returns_pair")
    await app.run_worker(run_once=True)

    typed = await app.get_result(job_id, result_model=Pair)
    assert typed == Pair(a=1, b=2)

    await app.close()


@pytest.mark.asyncio
async def test_retry_policy_can_short_circuit_to_dead_letter():
    """A RetryPolicy that returns None should dead-letter the job
    immediately, even if there's still retry budget remaining."""

    class _NoRetry:
        def delay_for(self, *, attempt, job_meta, exc):
            return None

    backend = MemoryBackend()
    await backend.initialize()
    registry = JobRegistry()

    async def always_fails():
        raise RuntimeError("nope")

    registry.register_job(always_fails, name=always_fails.__name__, max_retries=5)
    job_name = always_fails.__name__

    await backend.create_job(
        job_id="dl-policy",
        job_name=job_name,
        args={},
        args_hash=None,
        max_attempts=6,
        priority=100,
        queue="default",
        unique=False,
        dedup_key=None,
        scheduled_at=None,
    )

    await process_job_via_backend(
        backend=backend,
        job_registry=registry,
        queues=["default"],
        retry_policy=_NoRetry(),
    )

    assert await backend.get_job("dl-policy") is None
    dlq_row = backend._dead_letter_jobs["dl-policy"]
    assert "Retry policy declined" in dlq_row["last_error"]
