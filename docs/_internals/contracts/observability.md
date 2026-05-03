# Observability seam

Soniq has exactly one metrics surface as of 0.0.2: the
`MetricsSink` Protocol in `soniq.observability`. The legacy
`soniq.features.metrics` analytics stack (`MetricsCollector`,
`MetricsAnalyzer`, `AlertManager`, `MetricsService`) has been removed,
along with the `soniq metrics` CLI subcommand.

## What the seam is

`MetricsSink` is a runtime callback pair the worker invokes around every
job execution:

| Callback           | When                                                    |
| ------------------ | ------------------------------------------------------- |
| `record_job_start` | After claim, before the handler runs                    |
| `record_job_end`   | After the handler returns, raises, or `Snooze(...)`s    |

Status values passed to `record_job_end`:

- `"done"`         handler returned normally
- `"failed"`       handler raised; worker will retry per `RetryPolicy`
- `"dead_letter"`  retries exhausted, or `RetryPolicy` returned `None`
- `"snoozed"`      handler returned `Snooze(...)`; not a retry burn

The Protocol is intentionally narrow. Sinks should treat each call as
fire-and-forget, and must not raise: an exception from the sink
propagates and marks the job as failed even if the handler succeeded.

## What the seam is not

`MetricsSink` is *events out*. It does not store, aggregate, or query
historical state. Operators who need historical rollups have two
choices:

- Run `PrometheusMetricsSink` (`prometheus_client` ships with the
  default install) and let Prometheus + Grafana do the aggregation.
- Query the `soniq_*` lifecycle tables directly. The dashboard's
  `/api/job-stats`, `/api/queue-stats`, and friends already expose the
  most common rollups; SQL against `soniq_jobs` and
  `soniq_dead_letter_jobs` is the source of record beyond that.

Soniq does not ship its own time-series store, alert manager, or
dashboard analytics service. That overlap was the
`features.metrics` stack and it has been removed.

## Wiring a sink

```python
from soniq import Soniq
from soniq.observability import PrometheusMetricsSink

app = Soniq(
    database_url="postgresql://localhost/myapp",
    metrics_sink=PrometheusMetricsSink(),
)
```

The default is `NoopMetricsSink` (zero overhead, no `prometheus_client`
import). Custom sinks satisfy the Protocol by exposing async
`record_job_start` / `record_job_end` methods with the documented
signature; runtime checks use `isinstance(sink, MetricsSink)`.

## Stability

The Protocol may grow new optional methods (queue depth, per-queue
throughput) in 0.0.2+. New methods will be added with default no-op
implementations on `NoopMetricsSink` and `PrometheusMetricsSink`, so
existing custom sinks don't need to change.
