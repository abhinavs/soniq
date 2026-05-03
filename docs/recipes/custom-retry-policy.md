# Recipe: Custom Retry Policy

The default retry behavior - exponential backoff with full jitter - is the right shape for most jobs. But some jobs need to react to specific errors:

- A `RateLimitError` carrying a `retry_after` attribute should wait that exact duration.
- An `AuthenticationError` should never retry; dead-letter immediately so an operator can rotate the credential.
- A network blip during a third-party call should retry quickly without burning the full backoff curve.

That logic belongs in a `RetryPolicy`, not in the job handler. The handler raises a typed exception; the policy translates exceptions to delays.

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

`attempt` is 1-based and reflects the count after the dequeue bump. `job_meta` is the registry config dict (`retry_delay`, `retry_backoff`, `retry_max_delay`, plus any custom keys passed to `@app.job(...)`). `exc` is the exception that surfaced from the handler; it is a synthetic `RuntimeError` whose message is the rendered traceback. Inspect `str(exc)` rather than `type(exc)`.

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

        # Honor a Retry-After hint when the upstream provided one.
        match = _RETRY_AFTER_RE.search(msg)
        if match:
            return float(match.group(1))

        # Everything else: defer to the default exponential backoff.
        return _DEFAULT.delay_for(attempt=attempt, job_meta=job_meta, exc=exc)


app = Soniq(
    database_url="postgresql://localhost/myapp",
    retry_policy=RateLimitAwarePolicy(),
)
```

The handler stays simple: raise `RateLimitError("rate-limited; retry_after=30")` and the policy picks up `30.0` from the message.

## Skipping retries entirely (dead-letter on first failure)

```python
class NoRetry:
    def delay_for(self, *, attempt, job_meta, exc):
        return None


app = Soniq(retry_policy=NoRetry())
```

Useful for jobs where any failure represents corrupt data and a retry would burn database cycles for nothing.

## Combining with the registry's `max_retries`

The policy is consulted only after the handler raises. The dequeue-time check that compares `attempts` to `max_attempts` (set by `@app.job(max_retries=N)`) still applies: a policy that always returns `0.5` on a job with `max_retries=3` will retry 3 times then dead-letter regardless. To dead-letter earlier, return `None` from the policy.
