"""
`MetricsSink` Protocol + a no-op default.

The sink receives two callbacks per job: `record_job_start` when the
worker claims a job and is about to call the handler, and
`record_job_end` when the handler returns or raises. Implementations
should treat both calls as fire-and-forget; raising propagates and will
mark the job as failed even if the handler succeeded.

Status values passed to `record_job_end`:

- `"done"`         handler returned normally
- `"failed"`       handler raised; the worker will retry per RetryPolicy
- `"dead_letter"`  retries exhausted (or RetryPolicy returned None)
- `"snoozed"`      handler returned `Snooze(...)`; not a retry burn

Stability: this Protocol may grow new optional methods in 0.0.2+ as
more events are introduced (queue depth, per-queue throughput). New
methods will be added with default no-op implementations on the base
classes shipped here, so existing custom sinks don't need to change.
"""

from typing import Optional, Protocol, runtime_checkable


@runtime_checkable
class MetricsSink(Protocol):
    """Pluggable metrics destination for per-job observability."""

    async def record_job_start(
        self,
        *,
        job_id: str,
        job_name: str,
        queue: str,
        attempt: int,
    ) -> None: ...

    async def record_job_end(
        self,
        *,
        job_id: str,
        job_name: str,
        queue: str,
        status: str,
        duration_s: float,
        error: Optional[str] = None,
    ) -> None: ...


class NoopMetricsSink:
    """Default sink: silent, zero overhead.

    Soniq uses this when the user does not pass `metrics_sink=`. Production
    deployments that care about observability replace it with
    `PrometheusMetricsSink` or a custom implementation.
    """

    async def record_job_start(
        self,
        *,
        job_id: str,
        job_name: str,
        queue: str,
        attempt: int,
    ) -> None:
        return None

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
        return None


# Module-level default. Soniq uses this when the user does not pass a
# `metrics_sink=`.
DEFAULT_METRICS_SINK: MetricsSink = NoopMetricsSink()
