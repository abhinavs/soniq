# Hooks

Hooks let you run code before and after every job, and when a job fails.
They are useful for logging, metrics, tracing, and alerting without modifying
your job functions.


## Registering hooks

Use the decorator methods on your `Soniq` instance:

```python
app = Soniq(database_url="postgresql://localhost/myapp")

@app.before_job
async def log_start(job_name: str, job_id: str, attempt: int):
    print(f"Starting {job_name} ({job_id}), attempt {attempt}")

@app.after_job
async def log_success(job_name: str, job_id: str, duration_ms: float):
    print(f"Completed {job_name} ({job_id}) in {duration_ms}ms")

@app.on_error
async def log_failure(job_name: str, job_id: str, error: str, attempt: int):
    print(f"Failed {job_name} ({job_id}): {error} (attempt {attempt})")
```

You can register multiple hooks for the same event. They run in registration order.


## Hook signatures

### @app.before_job

Called immediately before a job function executes.

```python
async def before_job_hook(job_name: str, job_id: str, attempt: int) -> None
```

| Argument | Type | Description |
|---|---|---|
| `job_name` | `str` | Fully qualified job name (`module.function`). |
| `job_id` | `str` | UUID of the job. |
| `attempt` | `int` | Current attempt number. |

### @app.after_job

Called after a job completes successfully.

```python
async def after_job_hook(job_name: str, job_id: str, duration_ms: float) -> None
```

| Argument | Type | Description |
|---|---|---|
| `job_name` | `str` | Fully qualified job name. |
| `job_id` | `str` | UUID of the job. |
| `duration_ms` | `float` | Wall-clock execution time in milliseconds. |

### @app.on_error

Called when a job raises an exception. Runs before the retry/dead-letter decision.

```python
async def on_error_hook(job_name: str, job_id: str, error: str, attempt: int) -> None
```

| Argument | Type | Description |
|---|---|---|
| `job_name` | `str` | Fully qualified job name. |
| `job_id` | `str` | UUID of the job. |
| `error` | `str` | String representation of the exception. |
| `attempt` | `int` | The attempt number that failed. |


## Execution order

For a successful job:

```
before_job -> job function -> after_job
```

For a failed job:

```
before_job -> job function (raises) -> on_error
```

`after_job` and `on_error` are mutually exclusive for a given execution.
`before_job` always runs, even if the job will ultimately fail.


## Sync and async hooks

Hooks can be either sync or async. Soniq inspects each hook at call time
and awaits it if it is a coroutine function.

```python
# Async hook
@app.before_job
async def async_hook(job_name, job_id, attempt):
    await some_async_operation()

# Sync hook works too
@app.before_job
def sync_hook(job_name, job_id, attempt):
    some_sync_operation()
```


## Error handling in hooks

If a hook raises an exception, Soniq logs a warning and continues. A broken
hook never prevents a job from running or its result from being recorded.

```
WARNING - Hook before_job failed: ConnectionError(...)
```

This means hooks are safe for non-critical operations like metrics and logging.
If you need a hook failure to stop job execution, handle that logic inside the
job function itself.


## Practical examples

### Structured logging

```python
import logging
import json

logger = logging.getLogger("soniq.hooks")

@app.before_job
async def structured_log_start(job_name, job_id, attempt):
    logger.info(json.dumps({
        "event": "job_started",
        "job_name": job_name,
        "job_id": job_id,
        "attempt": attempt,
    }))

@app.after_job
async def structured_log_done(job_name, job_id, duration_ms):
    logger.info(json.dumps({
        "event": "job_completed",
        "job_name": job_name,
        "job_id": job_id,
        "duration_ms": duration_ms,
    }))
```

### Prometheus metrics

```python
from prometheus_client import Counter, Histogram

jobs_started = Counter("soniq_jobs_started", "Jobs started", ["job_name"])
jobs_completed = Histogram("soniq_jobs_duration_ms", "Job duration", ["job_name"])
jobs_failed = Counter("soniq_jobs_failed", "Jobs failed", ["job_name"])

@app.before_job
async def track_start(job_name, job_id, attempt):
    jobs_started.labels(job_name=job_name).inc()

@app.after_job
async def track_duration(job_name, job_id, duration_ms):
    jobs_completed.labels(job_name=job_name).observe(duration_ms)

@app.on_error
async def track_failure(job_name, job_id, error, attempt):
    jobs_failed.labels(job_name=job_name).inc()
```

### Slack alerting on dead-letter

```python
@app.on_error
async def alert_on_final_failure(job_name, job_id, error, attempt):
    # Look up the job's max_attempts from the registry
    job_meta = app._get_job_registry().get_job(job_name)
    if job_meta and attempt >= job_meta["max_retries"] + 1:
        await send_slack_message(
            f"Job {job_name} ({job_id}) moved to dead-letter queue: {error}"
        )
```

### OpenTelemetry tracing

```python
from opentelemetry import trace

tracer = trace.get_tracer("soniq")
_spans = {}

@app.before_job
async def start_span(job_name, job_id, attempt):
    span = tracer.start_span(f"job:{job_name}", attributes={
        "job.id": job_id,
        "job.attempt": attempt,
    })
    _spans[job_id] = span

@app.after_job
async def end_span_success(job_name, job_id, duration_ms):
    span = _spans.pop(job_id, None)
    if span:
        span.set_attribute("job.duration_ms", duration_ms)
        span.end()

@app.on_error
async def end_span_error(job_name, job_id, error, attempt):
    span = _spans.pop(job_id, None)
    if span:
        span.set_status(trace.StatusCode.ERROR, error)
        span.end()
```
