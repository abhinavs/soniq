"""
Pluggable observability surface for Soniq.

The `MetricsSink` Protocol formalizes the per-job metrics callbacks the
worker emits. The default is `NoopMetricsSink` (zero overhead, no
collectors registered). To wire up Prometheus:

    from soniq import Soniq
    from soniq.observability import PrometheusMetricsSink

    app = Soniq(
        database_url="postgresql://localhost/myapp",
        metrics_sink=PrometheusMetricsSink(),
    )

`prometheus_client` is a default dependency of `soniq` (batteries
included), so this module imports cleanly from a plain
`pip install soniq`. Nothing is registered until a sink is constructed.
"""

from .metrics import MetricsSink, NoopMetricsSink
from .prometheus import PrometheusMetricsSink

__all__ = ["MetricsSink", "NoopMetricsSink", "PrometheusMetricsSink"]
