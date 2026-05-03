"""Public type aliases shared across the Soniq surface.

Anything in this module is intentionally part of the public API: tests,
backends, dashboard, and CLI all import from here. Keep additions narrow
and tied to a contract in ``docs/_internals/contracts/``.
"""

from typing import TypedDict


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
