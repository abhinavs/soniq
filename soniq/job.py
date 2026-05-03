"""
Job model, status enum, and runtime context.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


@dataclass(frozen=True)
class Snooze:
    """
    Return value for a job handler that wants to defer without consuming a retry.

    Returning `Snooze(seconds=N, reason="...")` from a handler re-schedules
    the job to run again in N seconds with the attempts counter unchanged.
    Use it for rate-limited APIs (e.g. HTTP 429), webhook backpressure, or
    any "not ready yet" condition where a retry would be wasted.

    The duration is capped by the `snooze_max_seconds` setting to prevent
    a runaway handler from scheduling a job arbitrarily far into the future.
    """

    seconds: float
    reason: Optional[str] = field(default=None)


class JobStatus(str, Enum):
    """Job lifecycle statuses for ``soniq_jobs.status``.

    See ``docs/_internals/contracts/job_lifecycle.md``. The four live values are
    the only ones any backend ever writes to ``soniq_jobs``. Failures
    either re-queue (``status`` flips back to ``queued``) or move
    rows into ``soniq_dead_letter_jobs``; there is no ``failed`` or
    ``dead_letter`` row state.
    """

    QUEUED = "queued"
    PROCESSING = "processing"
    DONE = "done"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class JobContext:
    """
    Runtime metadata about the currently executing job.

    Injected automatically into job functions that declare a
    parameter with this type annotation.

    Example:
        @app.job()
        async def process_order(order_id: str, ctx: JobContext):
            print(f"Job {ctx.job_id}, attempt {ctx.attempt}")

    `worker_id` is always a string (empty when the context is built outside
    a worker claim, e.g. from the logging helper). `scheduled_at` and
    `created_at` are genuinely optional: `scheduled_at` is None for
    immediately-queued jobs, and the logging context may not carry
    `created_at`.
    """

    job_id: str
    job_name: str
    attempt: int
    max_attempts: int
    queue: str
    worker_id: str = ""
    scheduled_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
