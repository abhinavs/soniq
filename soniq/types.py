"""Public type aliases shared across the Soniq surface.

Anything in this module is intentionally part of the public API: tests,
backends, dashboard, and CLI all import from here. Keep additions narrow
and tied to a contract in ``docs/_internals/contracts/``.
"""

from datetime import datetime, timedelta
from typing import (
    Any,
    Coroutine,
    Dict,
    Optional,
    ParamSpec,
    Protocol,
    TypedDict,
    TypeVar,
    Union,
)

_P = ParamSpec("_P")
_R_co = TypeVar("_R_co", covariant=True)


class QueueStats(TypedDict):
    """Whole-instance job state counts.

    The single canonical shape for ``backend.get_queue_stats()`` and
    ``Soniq.get_queue_stats()``. Six closed-form keys, no dynamic
    fields, no per-queue breakdown - per-queue rollups belong in a
    separate query if a future caller needs them.

    Cross-table aggregation: ``queued``/``processing``/``done``/``cancelled``
    come from ``soniq_jobs`` GROUP BY status. ``dead_letter`` counts rows
    in the separate ``soniq_dead_letter_jobs`` table (DLQ Option A;
    ``soniq_jobs.status='dead_letter'`` is rejected at the schema level).
    ``total`` is the sum of all five state counts.

    See ``docs/_internals/contracts/queue_stats.md``.
    """

    total: int
    queued: int
    processing: int
    done: int
    dead_letter: int
    cancelled: int


class JobOptions(Protocol[_P]):
    """What ``job.options(...)`` returns: an ``enqueue`` with fixed options.

    The job's own arguments are type-checked against the job signature.
    Pass them as keywords; positional arguments raise ``TypeError``.
    """

    def enqueue(
        self, *args: _P.args, **kwargs: _P.kwargs
    ) -> Coroutine[Any, Any, str]: ...


class JobFunction(Protocol[_P, _R_co]):
    """Static type of a function decorated with ``@soniq.job()``.

    This is a typing aid only. At runtime the decorator returns a plain
    function (a ``functools.wraps`` wrapper) with these extra attributes.
    A ``JobFunction`` is still a ``Callable[P, Awaitable[R]]``, so code
    that calls the job directly or passes it to ``soniq.enqueue()``
    type-checks as before.
    """

    __name__: str
    __qualname__: str
    __module__: str
    _soniq_name: str
    _soniq_config: Dict[str, Any]

    def __call__(
        self, *args: _P.args, **kwargs: _P.kwargs
    ) -> Coroutine[Any, Any, _R_co]: ...

    def enqueue(self, *args: _P.args, **kwargs: _P.kwargs) -> Coroutine[Any, Any, str]:
        """Enqueue this job. Same as ``soniq.enqueue(job, **kwargs)``."""
        ...

    def options(
        self,
        *,
        queue: Optional[str] = None,
        priority: Optional[int] = None,
        scheduled_at: Union[datetime, timedelta, int, float, None] = None,
        unique: Optional[bool] = None,
        dedup_key: Optional[str] = None,
        connection: Optional[Any] = None,
    ) -> JobOptions[_P]:
        """Return a handle whose ``enqueue`` uses these enqueue options."""
        ...
