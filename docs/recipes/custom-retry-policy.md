---
description: "Write a custom RetryPolicy for Soniq Python background jobs: honor Retry-After hints, dead-letter on auth errors, or skip retries for a PostgreSQL job queue."
---

# Recipe: Custom Retry Policy

Soniq retries a failed job with the delays you set in `@soniq.job()`. Some jobs need to react to specific errors instead. A custom `RetryPolicy` sets the delay for each failure, or dead-letters the job at once. These cases are common:

- A `RateLimitError` carrying a `retry_after` attribute should wait that exact duration.
- An `AuthenticationError` should never retry; dead-letter immediately so an operator can rotate the credential.
- A network blip during a third-party call should retry quickly without burning the full backoff curve.

That logic belongs in a `RetryPolicy`, not in the handler. The handler raises an exception. The policy turns the exception message into a delay.

## Protocol

```python
# soniq/core/retry.py
from typing import Protocol, runtime_checkable, Optional


@runtime_checkable
class RetryPolicy(Protocol):
    def delay_for(
        self,
        *,
        attempt: int,
        job_meta: dict,
        exc: BaseException,
    ) -> Optional[float]:
        """Return delay seconds for the next attempt.
        Return None to dead-letter immediately."""
```

`attempt` is 1-based and reflects the count after the dequeue bump. `job_meta` is the registry config dict (`retry_delay`, `retry_backoff`, `retry_max_delay`, plus any custom keys passed to `@soniq.job(...)`). `exc` is the exception that surfaced from the handler; it is a synthetic `RuntimeError` whose message is the rendered traceback. Inspect `str(exc)`, not `type(exc)`.

## Example: rate-limit-aware policy

```python
import re

from soniq import Soniq
from soniq.core.retry import RetryPolicy, ExponentialBackoff


_DEFAULT = ExponentialBackoff()
_RETRY_AFTER_RE = re.compile(r"retry[_-]after[:= ]\s*(\d+(?:\.\d+)?)")


class RateLimitAwarePolicy:
    def delay_for(self, *, attempt, job_meta, exc):
        msg = str(exc).lower()

        # Authentication errors are not retryable.
        if "authentication" in msg or "401" in msg:
            return None

        # Use the Retry-After hint when the upstream provided one.
        match = _RETRY_AFTER_RE.search(msg)
        if match:
            return float(match.group(1))

        # Everything else: defer to the default exponential backoff.
        return _DEFAULT.delay_for(attempt=attempt, job_meta=job_meta, exc=exc)


soniq = Soniq(
    database_url="postgresql://localhost/myapp",
    retry_policy=RateLimitAwarePolicy(),
)
```

The handler stays simple. It raises `RateLimitError("rate-limited; retry_after=30")`, and the policy reads `30.0` from the message.

## Skipping retries entirely (dead-letter on first failure)

```python
class NoRetry:
    def delay_for(self, *, attempt, job_meta, exc):
        return None


soniq = Soniq(retry_policy=NoRetry())
```

Use this for jobs where any failure means corrupt data and a retry cannot help.

## Combining with the job's `retries` limit

Soniq calls the policy only after the handler raises. The attempt limit from `@soniq.job(retries=N)` still applies. A policy that always returns `0.5` on a job with `retries=3` retries three times, then dead-letters the job. To dead-letter earlier, return `None` from the policy.
