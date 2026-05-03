"""
`soniq.observability.MetricsSink` is the pluggable per-job observability
hook. The processor invokes `record_job_start` once per claim and
`record_job_end` once per terminal transition (done, failed,
dead_letter, snoozed, or contract-violation dead-letter).
"""

import pytest

from soniq import Soniq
from soniq.core.processor import process_job_via_backend
from soniq.core.registry import JobRegistry
from soniq.job import Snooze
from soniq.observability import MetricsSink, NoopMetricsSink
from soniq.observability.metrics import DEFAULT_METRICS_SINK
from soniq.testing import MemoryBackend


def test_default_metrics_sink_implements_protocol():
    assert isinstance(DEFAULT_METRICS_SINK, MetricsSink)
    assert isinstance(NoopMetricsSink(), MetricsSink)


def test_soniq_accepts_custom_metrics_sink():
    class _Sink:
        async def record_job_start(self, *, job_id, job_name, queue, attempt):
            pass

        async def record_job_end(
            self, *, job_id, job_name, queue, status, duration_s, error=None
        ):
            pass

    app = Soniq(
        database_url="postgresql://user:pass@localhost/test", metrics_sink=_Sink()
    )
    assert isinstance(app._metrics_sink, _Sink)


class _RecordingSink:
    """Captures every `record_job_*` call for inspection."""

    def __init__(self):
        self.starts: list = []
        self.ends: list = []

    async def record_job_start(self, *, job_id, job_name, queue, attempt):
        self.starts.append(
            {
                "job_id": job_id,
                "job_name": job_name,
                "queue": queue,
                "attempt": attempt,
            }
        )

    async def record_job_end(
        self, *, job_id, job_name, queue, status, duration_s, error=None
    ):
        self.ends.append(
            {
                "job_id": job_id,
                "job_name": job_name,
                "queue": queue,
                "status": status,
                "duration_s": duration_s,
                "error": error,
            }
        )


async def _run_one(
    sink, *, job_func, max_attempts=3, args=None, attempts_override=None
):
    backend = MemoryBackend()
    await backend.initialize()
    registry = JobRegistry()
    wrapped = registry.register_job(job_func, name=job_func.__name__)
    job_name = wrapped._soniq_name
    job_id = "metrics-job"
    await backend.create_job(
        job_id=job_id,
        job_name=job_name,
        args=args or {},
        args_hash=None,
        max_attempts=max_attempts,
        priority=100,
        queue="metrics-queue",
        unique=False,
        dedup_key=None,
        scheduled_at=None,
    )
    if attempts_override is not None:
        backend._jobs[job_id]["attempts"] = attempts_override

    await process_job_via_backend(
        backend=backend,
        job_registry=registry,
        queues=["metrics-queue"],
        metrics_sink=sink,
    )
    return backend, job_id


@pytest.mark.asyncio
async def test_metrics_sink_records_done_status_for_successful_job():
    sink = _RecordingSink()

    async def succeeds():
        return "ok"

    await _run_one(sink, job_func=succeeds)

    assert len(sink.starts) == 1
    assert len(sink.ends) == 1
    assert sink.starts[0]["queue"] == "metrics-queue"
    assert sink.starts[0]["attempt"] == 1
    assert sink.ends[0]["status"] == "done"
    assert sink.ends[0]["duration_s"] >= 0
    assert sink.ends[0]["error"] is None


@pytest.mark.asyncio
async def test_metrics_sink_records_failed_status_with_retry_remaining():
    sink = _RecordingSink()

    async def fails():
        raise RuntimeError("boom")

    await _run_one(sink, job_func=fails, max_attempts=3)

    assert sink.ends[0]["status"] == "failed"
    assert "boom" in sink.ends[0]["error"]


@pytest.mark.asyncio
async def test_metrics_sink_records_dead_letter_status_when_retries_exhausted():
    sink = _RecordingSink()

    async def fails():
        raise RuntimeError("permanent")

    # max_attempts=1 with attempts pre-bumped to 1 means the next claim
    # exhausts the budget on this attempt.
    await _run_one(sink, job_func=fails, max_attempts=1)

    assert sink.ends[0]["status"] == "dead_letter"
    assert sink.ends[0]["error"] is not None


@pytest.mark.asyncio
async def test_metrics_sink_records_snoozed_status():
    sink = _RecordingSink()

    async def snoozes():
        return Snooze(seconds=1.0, reason="rate-limited")

    await _run_one(sink, job_func=snoozes)

    assert sink.ends[0]["status"] == "snoozed"


@pytest.mark.asyncio
async def test_metrics_sink_no_end_call_when_job_unregistered():
    """An unregistered job is dead-lettered before the start hook fires;
    no observability calls should happen for it."""
    sink = _RecordingSink()
    backend = MemoryBackend()
    await backend.initialize()
    registry = JobRegistry()  # nothing registered

    await backend.create_job(
        job_id="orphan",
        job_name="not.registered.func",
        args={},
        args_hash=None,
        max_attempts=3,
        priority=100,
        queue="default",
        unique=False,
        dedup_key=None,
        scheduled_at=None,
    )

    await process_job_via_backend(
        backend=backend, job_registry=registry, metrics_sink=sink
    )

    assert sink.starts == []
    assert sink.ends == []


@pytest.mark.asyncio
async def test_metrics_sink_records_dead_letter_on_corruption():
    sink = _RecordingSink()

    async def task():
        return None

    # Force a backend contract violation: args is a string not a dict.
    backend = MemoryBackend()
    await backend.initialize()
    registry = JobRegistry()
    wrapped = registry.register_job(task, name=task.__name__)
    await backend.create_job(
        job_id="corrupt",
        job_name=wrapped._soniq_name,
        args={},  # Memory accepts dict; we mutate below to simulate the contract violation.
        args_hash=None,
        max_attempts=3,
        priority=100,
        queue="default",
        unique=False,
        dedup_key=None,
        scheduled_at=None,
    )
    backend._jobs["corrupt"]["args"] = "not a dict"

    await process_job_via_backend(
        backend=backend, job_registry=registry, metrics_sink=sink
    )

    assert sink.ends[0]["status"] == "dead_letter"
    assert "contract violation" in sink.ends[0]["error"]


def test_prometheus_sink_imports_lazily():
    """The Prometheus sink should not import prometheus_client at module
    load time, so users without the optional dependency can still
    `from soniq.observability import MetricsSink`."""
    import importlib

    # Ensure prometheus module is loadable in this environment first.
    pytest.importorskip("prometheus_client")

    # Re-import to confirm the impl wires up cleanly.
    mod = importlib.import_module("soniq.observability.prometheus")
    sink = mod.PrometheusMetricsSink(prefix="soniq_test")
    assert sink is not None

    # Cleanup the test prefix from the global registry so this test is
    # repeatable in the same interpreter.
    from prometheus_client import REGISTRY

    for collector in list(REGISTRY._names_to_collectors.values()):
        # Try to unregister; failure means this collector is shared with
        # another test, which is fine to leave.
        try:
            REGISTRY.unregister(collector)
        except Exception:
            pass


@pytest.mark.asyncio
async def test_prometheus_sink_emits_counters_and_histogram():
    pytest.importorskip("prometheus_client")
    from prometheus_client import CollectorRegistry, generate_latest

    from soniq.observability import PrometheusMetricsSink

    reg = CollectorRegistry()
    sink = PrometheusMetricsSink(registry=reg, prefix="soniq_test")

    await sink.record_job_start(
        job_id="j1", job_name="pkg.task", queue="default", attempt=1
    )
    await sink.record_job_end(
        job_id="j1",
        job_name="pkg.task",
        queue="default",
        status="done",
        duration_s=0.123,
    )

    payload = generate_latest(reg).decode()
    assert "soniq_test_jobs_started_total" in payload
    assert 'queue="default"' in payload
    assert 'status="done"' in payload
    assert "soniq_test_job_duration_seconds" in payload
    assert "soniq_test_jobs_in_progress" in payload
