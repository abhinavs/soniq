"""
Pluggable storage backends for Soniq.

The backend interface is split into three Protocols, each describing
one capability surface:

- ``JobStore``: enqueue, dequeue, status transitions, queries, listen
  / notify, lifecycle. Required of every backend.
- ``WorkerStore``: worker tracking and heartbeat housekeeping.
- ``TaskRegistryStore``: observability metadata about which workers
  handle which task names.

``StorageBackend`` is the marker Protocol for backends that implement
all three. Production-tier implementations:

- ``PostgresBackend`` (production)
- ``SQLiteBackend`` (local dev, zero setup)

The in-memory backend (``MemoryBackend``) lives under ``soniq.testing``
to make its scope obvious at the import site - it is for tests, examples,
and quick scripts only.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional, Protocol, runtime_checkable

if TYPE_CHECKING:
    from soniq.types import QueueStats


@runtime_checkable
class ListenerHandle(Protocol):
    """Opaque handle returned by ``listen_for_jobs``.

    Backends own the underlying transport (asyncpg connection,
    in-process callback registry, etc.) and expose only ``close()`` so
    callers cannot reach in and leak the connection. The worker shutdown
    path calls ``await handle.close()``; backends without push-notify
    return a no-op handle.
    """

    async def close(self) -> None:
        """Tear down the listener and release any held resources."""
        ...


@runtime_checkable
class JobStore(Protocol):
    """Enqueue, dequeue, status transitions, queries, listen/notify.

    Required of every backend. Capability flags here describe job-path
    behavior (push notify, transactional enqueue, advisory locks);
    callers gate on Protocol membership for everything else and on
    ``isinstance(backend, PostgresBackend)`` for the two genuinely
    Postgres-only paths (migrations, dashboard data).
    """

    # --- Capabilities ---

    @property
    def supports_push_notify(self) -> bool:
        """Whether this backend supports push notifications (LISTEN/NOTIFY)."""
        ...

    @property
    def supports_transactional_enqueue(self) -> bool:
        """Whether this backend supports transactional enqueue via connection=."""
        ...

    @property
    def supports_advisory_locks(self) -> bool:
        """Whether this backend supports Postgres-style advisory locks.

        True only for Postgres. Leadership election (`with_advisory_lock`)
        falls back to always-leader when this is False.
        """
        ...

    # --- Lifecycle ---

    async def initialize(self) -> None:
        """Create tables, pools, or other resources."""
        ...

    async def close(self) -> None:
        """Release all resources."""
        ...

    # --- Job CRUD ---

    async def create_job(
        self,
        *,
        job_id: str,
        job_name: str,
        args: dict,
        args_hash: Optional[str],
        max_attempts: int,
        priority: int,
        queue: str,
        unique: bool,
        dedup_key: Optional[str] = None,
        scheduled_at: Optional[datetime] = None,
        producer_id: Optional[str] = None,
    ) -> Optional[str]:
        """
        Insert a job. Return job_id on success.

        `args` is the unserialized job kwargs dict. Backends are responsible
        for any on-wire serialization they need; callers work with dicts.

        If unique=True and a duplicate queued job exists, return existing ID.
        If dedup_key is set and a locked queued job exists, return existing ID.
        """
        ...

    # --- Worker dequeue ---

    async def fetch_and_lock_job(
        self,
        *,
        queues: Optional[list[str]],
        worker_id: Optional[str],
    ) -> Optional[dict]:
        """
        Atomically find the next eligible job, lock it, mark as 'processing'.
        Return job record dict or None.

        Must guarantee at-most-once claim per call across concurrent workers.
        """
        ...

    async def notify_new_job(self, queue: str) -> None:
        """
        Signal that a new job is available.

        Postgres: pg_notify. SQLite/Memory: no-op (polling only).
        """
        ...

    async def listen_for_jobs(
        self,
        callback: Any,
        channel: str = "soniq_new_job",
    ) -> "ListenerHandle":
        """
        Start listening for job notifications.

        Returns a ``ListenerHandle`` whose ``close()`` tears down both
        the subscription and any held connection. Backends without push
        notification return a no-op handle.
        """
        ...

    # --- Job status transitions ---

    async def mark_job_done(
        self,
        job_id: str,
        *,
        result_ttl: Optional[int] = None,
        result: Any = None,
    ) -> None:
        """Mark job as done. If result_ttl=0, delete immediately."""
        ...

    async def mark_job_failed(
        self,
        job_id: str,
        *,
        attempts: int,
        error: str,
        retry_delay: Optional[float] = None,
    ) -> None:
        """
        Mark job as failed.

        If retry_delay is set, reschedule to queued with delay.
        If retry_delay is None, reschedule immediately.
        """
        ...

    async def mark_job_dead_letter(
        self,
        job_id: str,
        *,
        attempts: int,
        error: str,
        reason: str,
        tags: Optional[dict] = None,
    ) -> None:
        """Move a job out of ``soniq_jobs`` and into ``soniq_dead_letter_jobs``.

        Single-transaction INSERT-then-DELETE. The DLQ row keeps the
        original ``soniq_jobs.id`` as primary key, with ``reason`` and
        ``tags`` populated from the call site. After commit the row
        exists in exactly one table. See ``docs/_internals/contracts/dead_letter.md``
        and ``docs/design/dlq_option_a.md``.
        """
        ...

    async def nack_job(self, job_id: str) -> None:
        """Abandon a claimed job back to ``queued`` (shutdown contract).

        Locked field set, identical across all backends:

        ::

            UPDATE soniq_jobs
            SET status='queued', worker_id=NULL,
                updated_at=NOW(), scheduled_at=NOW()
            WHERE id=$1 AND status='processing'

        ``attempts`` and ``last_error`` are **not** modified - the worker
        abandoned the job, the job did not fail. The WHERE clause makes
        the operation idempotent: a row that has already been advanced
        (e.g. by stale-worker recovery) is left alone.

        Called only on the async branch of ``FORCE_TIMEOUT_PATH``. Sync
        handlers never go through ``nack_job``; their rows are left in
        ``processing`` and reclaimed by stale-worker recovery in a
        subsequent process. See ``docs/_internals/contracts/shutdown.md``.
        """
        ...

    async def reschedule_job(
        self,
        job_id: str,
        *,
        delay_seconds: float,
        attempts: int,
        reason: Optional[str] = None,
    ) -> None:
        """
        Snooze a running job: set status back to 'queued', scheduled_at
        forward by delay_seconds, and attempts to the supplied value
        (callers typically pass the pre-claim count so the snooze does not
        consume a retry slot). Used by the Snooze return type.
        """
        ...

    async def cancel_job(self, job_id: str) -> bool:
        """Cancel a queued job. Return True if cancelled."""
        ...

    async def delete_job(self, job_id: str) -> bool:
        """Delete a job entirely. Return True if deleted."""
        ...

    # --- Queries ---

    async def get_job(self, job_id: str) -> Optional[dict]:
        """Fetch a single job by ID."""
        ...

    async def list_jobs(
        self,
        *,
        queue: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        """List jobs with optional filters."""
        ...

    async def get_queue_stats(self) -> "QueueStats":
        """Whole-instance job state counts in the canonical 6-key shape.

        Returns a single ``QueueStats`` dict (``soniq.types.QueueStats``)
        with keys ``total / queued / processing / done / dead_letter /
        cancelled``. ``dead_letter`` is sourced from the separate
        ``soniq_dead_letter_jobs`` table - DLQ Option A means
        ``soniq_jobs.status='dead_letter'`` no longer exists. See
        ``docs/_internals/contracts/queue_stats.md``.
        """
        ...

    # --- Maintenance ---

    async def delete_expired_jobs(self) -> int:
        """Delete done jobs past their expires_at. Return count."""
        ...

    async def reset(self) -> None:
        """
        Delete all jobs and workers. Used in test fixtures.

        Memory: clear dicts. Postgres: TRUNCATE. SQLite: DELETE FROM.
        """
        ...


@runtime_checkable
class WorkerStore(Protocol):
    """Worker tracking and heartbeat housekeeping.

    Backends that surface "which workers are alive" implement this. The
    worker run loop requires it; broker-only backends that want to skip
    worker tracking can implement ``JobStore`` alone and the
    ``Worker.run()`` path will refuse to start against them.
    """

    async def register_worker(
        self,
        *,
        worker_id: str,
        hostname: str,
        pid: int,
        queues: list[str],
        concurrency: int,
        metadata: Optional[dict] = None,
    ) -> None:
        """Register or update a worker record."""
        ...

    async def update_heartbeat(
        self,
        worker_id: str,
        metadata: Optional[dict] = None,
    ) -> None:
        """Touch the worker's heartbeat timestamp."""
        ...

    async def mark_worker_stopped(self, worker_id: str) -> None:
        """Mark a worker as stopped."""
        ...

    async def cleanup_stale_workers(
        self,
        stale_threshold_seconds: int,
    ) -> int:
        """
        Find workers with expired heartbeats.
        Mark them stopped. Reset their processing jobs to queued.
        Return count of cleaned workers.
        """
        ...


@runtime_checkable
class TaskRegistryStore(Protocol):
    """Observability metadata: which workers handle which task names.

    Optional. Used by the dashboard and ``soniq tasks check`` to surface
    deploy-skew (jobs queued under names no live worker registers).
    """

    async def register_task_name(
        self,
        *,
        task_name: str,
        worker_id: str,
        args_model_repr: Optional[str] = None,
    ) -> None:
        """Upsert a worker's registration for ``task_name``."""
        ...

    async def list_registered_task_names(self) -> list[dict]:
        """Return all (task_name, worker_id, last_seen_at, ...) rows."""
        ...


@runtime_checkable
class StorageBackend(JobStore, WorkerStore, TaskRegistryStore, Protocol):
    """Marker Protocol for backends that implement every capability.

    Production-tier backends (Postgres, SQLite, Memory) compose all
    three Protocols. Library code that needs the full surface types
    its argument as ``StorageBackend``; code that only needs one slice
    types it as the narrower Protocol.
    """

    ...
