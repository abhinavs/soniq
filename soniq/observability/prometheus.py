"""
`PrometheusMetricsSink`: a `MetricsSink` implementation that emits
Soniq's per-job events as Prometheus metrics.

`prometheus_client` is a default dependency of `soniq` as of 0.0.2, so
this module is importable from a plain `pip install soniq`. The sink
itself is dormant unless wired via `Soniq(metrics_sink=...)`; the
default is `NoopMetricsSink`, so a stock install does not register any
collectors.

Exported metrics (default prefix `soniq`):

- `soniq_jobs_started_total{queue,job_name}` (Counter)
    Incremented once per job claim, before the handler runs.

- `soniq_jobs_completed_total{queue,job_name,status}` (Counter)
    Incremented once per job end. `status` is one of
    `done | failed | dead_letter | snoozed`.

- `soniq_job_duration_seconds{queue,job_name,status}` (Histogram)
    Wall-clock duration from claim to end, bucketed for typical
    background-job latencies.

- `soniq_jobs_in_progress{queue,job_name}` (Gauge)
    Increments on `record_job_start`, decrements on `record_job_end`.

Pass a custom `prefix` to namespace under e.g. `myapp_soniq_*`. Pass a
custom `registry` (a `prometheus_client.CollectorRegistry`) to keep
Soniq's metrics out of the default global registry; useful for tests
and for processes that expose multiple metric sets.
"""

from typing import Any, Optional

from prometheus_client import REGISTRY, Counter, Gauge, Histogram


class PrometheusMetricsSink:
    """Emit per-job events as Prometheus metrics.

    Usage:

        from prometheus_client import start_http_server
        from soniq import Soniq
        from soniq.observability import PrometheusMetricsSink

        sink = PrometheusMetricsSink()
        app = Soniq(database_url=..., metrics_sink=sink)

        # Expose metrics on http://localhost:9090/metrics
        start_http_server(9090)

    The sink is a thin shim over four Prometheus collectors. It does
    not start an HTTP server, scrape its own state, or push to a remote
    gateway - those are intentionally separate concerns. Use the
    `prometheus_client` helpers (`start_http_server`, push gateway,
    etc.) according to your scrape model.
    """

    def __init__(
        self,
        *,
        registry: Optional[Any] = None,
        prefix: str = "soniq",
    ):
        reg = registry if registry is not None else REGISTRY
        common_labels = ["queue", "job_name"]

        self._jobs_started = Counter(
            f"{prefix}_jobs_started_total",
            "Jobs claimed by a worker.",
            labelnames=common_labels,
            registry=reg,
        )
        self._jobs_completed = Counter(
            f"{prefix}_jobs_completed_total",
            "Jobs that have finished a single worker pass (any status).",
            labelnames=common_labels + ["status"],
            registry=reg,
        )
        self._duration = Histogram(
            f"{prefix}_job_duration_seconds",
            "Wall-clock duration from claim to end.",
            labelnames=common_labels + ["status"],
            registry=reg,
            # Buckets sized for typical background-job latencies: sub-100ms
            # webhook deliveries through multi-minute batch jobs.
            buckets=(
                0.005,
                0.01,
                0.025,
                0.05,
                0.1,
                0.25,
                0.5,
                1.0,
                2.5,
                5.0,
                10.0,
                30.0,
                60.0,
                120.0,
                300.0,
                600.0,
                float("inf"),
            ),
        )
        self._in_progress = Gauge(
            f"{prefix}_jobs_in_progress",
            "Jobs currently being processed by a worker.",
            labelnames=common_labels,
            registry=reg,
        )

    async def record_job_start(
        self,
        *,
        job_id: str,
        job_name: str,
        queue: str,
        attempt: int,
    ) -> None:
        self._jobs_started.labels(queue=queue, job_name=job_name).inc()
        self._in_progress.labels(queue=queue, job_name=job_name).inc()

    async def record_job_end(
        self,
        *,
        job_id: str,
        job_name: str,
        queue: str,
        status: str,
        duration_s: float,
        error: Optional[str] = None,
    ) -> None:
        self._jobs_completed.labels(queue=queue, job_name=job_name, status=status).inc()
        self._duration.labels(queue=queue, job_name=job_name, status=status).observe(
            duration_s
        )
        self._in_progress.labels(queue=queue, job_name=job_name).dec()
