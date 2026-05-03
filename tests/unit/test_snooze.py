"""
Unit tests for the Snooze return type.

Snooze lets a handler defer without consuming a retry slot. The processor
detects Snooze in the handler's return value and calls backend.reschedule_job
instead of backend.mark_job_done. The attempts counter is rolled back to its
pre-claim value so max_attempts is not burned.
"""

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest

from soniq.core.processor import process_job_via_backend
from soniq.core.registry import JobRegistry
from soniq.job import Snooze
from soniq.testing.memory_backend import MemoryBackend


async def _setup(job_func, max_attempts=5):
    backend = MemoryBackend()
    await backend.initialize()
    registry = JobRegistry()
    wrapped = registry.register_job(job_func, name=job_func.__name__)
    job_name = wrapped._soniq_name

    await backend.create_job(
        job_id="snooze-job",
        job_name=job_name,
        args={},
        args_hash=None,
        max_attempts=max_attempts,
        priority=100,
        queue="default",
        unique=False,
        dedup_key=None,
        scheduled_at=None,
    )
    return backend, registry


def test_snooze_is_frozen():
    s = Snooze(seconds=1.0)
    with pytest.raises(FrozenInstanceError):
        s.seconds = 2.0  # type: ignore[misc]


def test_snooze_defaults_reason_to_none():
    s = Snooze(seconds=5.0)
    assert s.reason is None


def test_snooze_accepts_reason():
    s = Snooze(seconds=5.0, reason="rate-limited")
    assert s.reason == "rate-limited"


@pytest.mark.asyncio
async def test_snoozed_job_is_requeued_with_attempts_unchanged():
    """Handler returns Snooze → job goes back to queued, attempts held at pre-claim value."""

    async def rate_limited_job():
        return Snooze(seconds=30.0, reason="429 too many requests")

    backend, registry = await _setup(rate_limited_job)
    processed = await process_job_via_backend(backend=backend, job_registry=registry)
    assert processed is True

    job = await backend.get_job("snooze-job")
    assert job["status"] == "queued"
    # Pre-claim attempts was 0; fetch_and_lock_job bumped to 1; snooze rolled back.
    assert job["attempts"] == 0
    assert job["scheduled_at"] is not None
    assert job["last_error"].startswith("SNOOZE: ")
    assert "429 too many requests" in job["last_error"]


@pytest.mark.asyncio
async def test_snooze_without_reason_writes_bare_marker():
    async def no_reason_snooze():
        return Snooze(seconds=10.0)

    backend, registry = await _setup(no_reason_snooze)
    await process_job_via_backend(backend=backend, job_registry=registry)

    job = await backend.get_job("snooze-job")
    assert job["last_error"] == "SNOOZE"


@pytest.mark.asyncio
async def test_snooze_exceeding_cap_is_clamped():
    """Snooze past snooze_max_seconds is silently capped."""
    from soniq.settings import SoniqSettings

    capped_settings = SoniqSettings(snooze_max_seconds=60.0)

    async def runaway_snooze():
        return Snooze(seconds=10_000.0, reason="ignored")

    backend, registry = await _setup(runaway_snooze)

    before = datetime.now(timezone.utc)
    await process_job_via_backend(
        backend=backend, job_registry=registry, settings=capped_settings
    )
    job = await backend.get_job("snooze-job")

    scheduled = datetime.fromisoformat(job["scheduled_at"])
    delta = (scheduled - before).total_seconds()
    # Cap is 60s; allow +5s slack for test scheduling jitter.
    assert delta <= 65


@pytest.mark.asyncio
async def test_non_snooze_return_value_still_marks_done():
    """Sanity: a handler returning a non-Snooze value still marks the job done."""

    async def normal_job():
        return {"ok": True}

    backend, registry = await _setup(normal_job, max_attempts=3)
    await process_job_via_backend(backend=backend, job_registry=registry)
    job = await backend.get_job("snooze-job")
    assert job["status"] == "done"


@pytest.mark.asyncio
async def test_snooze_does_not_burn_retry_after_max_attempts():
    """A snoozing job should never hit max_attempts because attempts stays at pre-claim."""
    count = {"n": 0}

    async def repeatedly_snooze():
        count["n"] += 1
        if count["n"] < 3:
            return Snooze(seconds=0.1)
        return "done"

    backend, registry = await _setup(repeatedly_snooze, max_attempts=2)

    # First two calls snooze; attempts stays 0. Third call completes.
    for _ in range(3):
        # Reset scheduled_at so the job is immediately pickable by fetch_and_lock_job.
        job = await backend.get_job("snooze-job")
        if job and job["status"] == "queued":
            backend._jobs["snooze-job"]["scheduled_at"] = None
        await process_job_via_backend(backend=backend, job_registry=registry)

    final = await backend.get_job("snooze-job")
    assert final["status"] == "done"
    assert count["n"] == 3
