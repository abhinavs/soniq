# Recipes

*Copy-paste starting points for real use cases.* Each recipe is a complete, working example for a specific job type - lift it into your codebase and adapt.

## Job patterns

- [Email jobs](email-jobs.md) - idempotent sending, escalating retries, dedicated queue
- [File processing](file-processing.md) - background uploads, CPU-bound work, long timeouts
- [Scheduled reports](scheduled-reports.md) - cron-based recurring jobs
- [Webhook delivery](webhook-delivery.md) - aggressive retries, idempotency tracking, payload signing

## Extension points

- [Custom retry policy](custom-retry-policy.md) - rate-limit-aware backoff, type-specific delays, no-retry mode
- [Custom metrics sink](custom-metrics-sink.md) - Prometheus, statsd, OpenTelemetry, or anything else

## Multi-service setups

- [Cross-service task stubs](cross-service-task-stubs.md) - producer enqueues a task it does not import; consumer in another service runs it
