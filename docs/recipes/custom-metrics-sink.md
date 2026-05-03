# Recipe: Custom Metrics Sink

Soniq emits two events per job execution: `record_job_start` when the worker claims a job and `record_job_end` when the handler returns or raises. Plug in a `MetricsSink` to forward those events to your monitoring system.

## Protocol

```python
# soniq/observability/metrics.py
from typing import Optional, Protocol, runtime_checkable


@runtime_checkable
class MetricsSink(Protocol):
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
```

`status` is one of `done` (handler returned), `failed` (handler raised; retries remaining), `dead_letter` (retries exhausted, retry policy declined, or backend contract violation), or `snoozed` (handler returned `Snooze(...)`; not a retry burn).

## Built-in: Prometheus

`prometheus_client` ships with the default `pip install soniq` (batteries-included as of 0.0.2). Then:

```python
from prometheus_client import start_http_server

from soniq import Soniq
from soniq.observability import PrometheusMetricsSink

app = Soniq(
    database_url="postgresql://localhost/myapp",
    metrics_sink=PrometheusMetricsSink(),
)

# Expose /metrics on http://localhost:9090
start_http_server(9090)
```

Metrics exported (default prefix `soniq`):

- `soniq_jobs_started_total{queue, job_name}` (Counter)
- `soniq_jobs_completed_total{queue, job_name, status}` (Counter)
- `soniq_job_duration_seconds{queue, job_name, status}` (Histogram)
- `soniq_jobs_in_progress{queue, job_name}` (Gauge)

Buckets on the Histogram are sized for typical background-job latencies (`5ms`, `10ms`, ..., `60s`, `120s`, `300s`, `600s`, `+Inf`).

### Custom prefix or registry

```python
from prometheus_client import CollectorRegistry

# Keep Soniq's metrics out of the default registry.
reg = CollectorRegistry()
sink = PrometheusMetricsSink(registry=reg, prefix="myapp_jobs")
app = Soniq(metrics_sink=sink)
```

The sink does not start an HTTP server, push to a gateway, or scrape its own state - those are intentionally separate concerns. Use the `prometheus_client` helpers (`start_http_server`, `push_to_gateway`) per your scrape model.

## Custom sink: forward to your platform

For Datadog, OpenTelemetry, statsd, or anything else, implement the Protocol directly:

```python
import time
from soniq import Soniq


class StatsdMetricsSink:
    """Emit per-job timings and counters to a statsd server."""

    def __init__(self, statsd_client):
        self._statsd = statsd_client

    async def record_job_start(self, *, job_id, job_name, queue, attempt):
        self._statsd.incr(
            "soniq.jobs.started",
            tags=[f"queue:{queue}", f"job:{job_name}"],
        )

    async def record_job_end(
        self, *, job_id, job_name, queue, status, duration_s, error=None
    ):
        tags = [f"queue:{queue}", f"job:{job_name}", f"status:{status}"]
        self._statsd.incr("soniq.jobs.completed", tags=tags)
        self._statsd.timing(
            "soniq.jobs.duration",
            duration_s * 1000,  # statsd convention: milliseconds
            tags=tags,
        )


import statsd  # or whatever client your platform ships
app = Soniq(metrics_sink=StatsdMetricsSink(statsd.StatsClient()))
```

## Combining with the noop default

The default sink is `NoopMetricsSink` - silent, zero overhead. Soniq treats `metrics_sink=` as a strict opt-in: passing `None` (or omitting the parameter) means "no metrics emitted." This is intentional. Observability dependencies are real production overhead and should be a deliberate choice, not a default.

## Caveats

- The sink is invoked by the worker, not by the enqueuer. `await app.enqueue(...)` does not call into the sink. If you need enqueue-time metrics, instrument your application code directly.
- Sink methods that raise will propagate up and fail the job. Wrap your sink in a try/except if you want metrics to be best-effort:

  ```python
  class BestEffortSink:
      def __init__(self, inner):
          self._inner = inner

      async def record_job_start(self, **kw):
          try:
              await self._inner.record_job_start(**kw)
          except Exception:
              pass  # don't fail jobs because metrics broke

      async def record_job_end(self, **kw):
          try:
              await self._inner.record_job_end(**kw)
          except Exception:
              pass
  ```
