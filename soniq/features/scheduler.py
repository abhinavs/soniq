"""Recurring-schedule service exposed as `app.scheduler`.

The Scheduler service owns:
  - persistence of recurring schedules (the `soniq_recurring_jobs` table on
    Postgres, an in-process dict on Memory/SQLite-backed apps),
  - the long-running poll loop guarded by an advisory-lock leader election
    so only one scheduler in a fleet evaluates due jobs per tick,
  - the atomic claim+enqueue+bookkeeping transaction that keeps
    "schedule advanced" and "job enqueued" inseparable on Postgres.

Public surface (called via `app.scheduler.<...>`):
    add(target, *, cron=None, every=None, args=None, queue=None,
        priority=None, max_attempts=None, name=None) -> str
    pause(name)
    resume(name)
    remove(name)
    list(status=None)
    get(name)
    start(check_interval=30)
    stop()
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, AsyncIterator, Optional, Union

from croniter import croniter  # type: ignore[import-untyped]

from soniq.backends.helpers import rows_affected
from soniq.backends.postgres import PostgresBackend
from soniq.core.leadership import with_advisory_lock
from soniq.core.naming import validate_task_name

if TYPE_CHECKING:
    from soniq.app import Soniq

logger = logging.getLogger(__name__)


# Dataclass-shaped record used in-memory and as the row payload for
# storage backends. Stored timestamps are timezone-aware UTC.
@dataclass
class _Schedule:
    id: str
    name: str
    schedule_type: str  # 'cron' | 'interval'
    schedule_value: str  # cron expr, or interval seconds as a string
    priority: int
    queue: str
    max_attempts: int
    args: dict
    status: str  # 'active' | 'paused'
    created_at: datetime
    last_run: Optional[datetime] = None
    next_run: Optional[datetime] = None
    run_count: int = 0
    last_job_id: Optional[str] = None


def _calculate_next_run(
    schedule_type: str, schedule_value: str, current_time: datetime
) -> Optional[datetime]:
    if schedule_type == "interval":
        try:
            secs = int(schedule_value)
        except (TypeError, ValueError):
            return None
        return current_time + timedelta(seconds=secs)
    if schedule_type == "cron":
        return croniter(schedule_value, current_time).get_next(datetime)  # type: ignore[no-any-return]
    return None


def _coerce_schedule(*, cron: Any, every: Any) -> tuple[str, str]:
    """Normalize the (cron, every) decorator/add inputs into (type, value)."""
    if cron is None and every is None:
        raise ValueError("Must specify either cron= or every=")
    if cron is not None and every is not None:
        raise ValueError("Cannot specify both cron= and every=")

    if cron is not None:
        expr = str(cron)  # builders override __str__
        if not croniter.is_valid(expr):
            raise ValueError(
                f"Invalid cron expression: {expr!r}. "
                "Expected a 5-field cron expression (e.g. '*/5 * * * *')."
            )
        return "cron", expr

    if isinstance(every, timedelta):
        secs = int(every.total_seconds())
    elif isinstance(every, (int, float)):
        secs = int(every)
    else:
        raise TypeError(
            f"every= must be a timedelta or seconds int; got {type(every).__name__}"
        )
    if secs < 1:
        raise ValueError("every= must be >= 1 second")
    return "interval", str(secs)


class _MemoryStore:
    """In-process schedule storage for backends without recurring DDL.

    Keyed by the schedule's stable name (the registered task name) so
    `pause/resume/remove` look identical to the SQL store. The store is a
    singleton per app and survives across `Scheduler` restarts.
    """

    def __init__(self) -> None:
        self._by_name: dict[str, _Schedule] = {}
        self._lock = asyncio.Lock()

    async def upsert(self, sched: _Schedule) -> None:
        async with self._lock:
            self._by_name[sched.name] = sched

    async def all(self) -> list[_Schedule]:
        async with self._lock:
            return [replace(s) for s in self._by_name.values()]

    async def get(self, name: str) -> Optional[_Schedule]:
        async with self._lock:
            s = self._by_name.get(name)
            return replace(s) if s is not None else None

    async def delete(self, name: str) -> bool:
        async with self._lock:
            return self._by_name.pop(name, None) is not None

    async def set_status(self, name: str, status: str) -> bool:
        async with self._lock:
            s = self._by_name.get(name)
            if s is None:
                return False
            s.status = status
            return True

    async def claim_and_advance(
        self, name: str, expected_next_run: datetime, new_next_run: datetime
    ) -> bool:
        async with self._lock:
            s = self._by_name.get(name)
            if s is None or s.next_run != expected_next_run:
                return False
            s.next_run = new_next_run
            return True

    async def record_run(
        self,
        name: str,
        *,
        last_run: datetime,
        run_count: int,
        last_job_id: Optional[str],
    ) -> None:
        async with self._lock:
            s = self._by_name.get(name)
            if s is None:
                return
            s.last_run = last_run
            s.run_count = run_count
            s.last_job_id = last_job_id


class _SqlStore:
    """Schedule storage backed by the `soniq_recurring_jobs` Postgres table.

    The table's `id` is a UUID, but the public API keys schedules by their
    stable task `name`. Re-adding the same name updates the existing row
    in place rather than duplicating it.
    """

    def __init__(self, backend: Any) -> None:
        self._backend = backend

    @asynccontextmanager
    async def _conn(self) -> AsyncIterator[Any]:
        async with self._backend.acquire() as conn:
            yield conn

    @staticmethod
    def _row_to_schedule(row: Any) -> _Schedule:
        return _Schedule(
            id=str(row["id"]),
            name=row["job_name"],
            schedule_type=row["schedule_type"],
            schedule_value=row["schedule_value"],
            priority=row["priority"],
            queue=row["queue"],
            max_attempts=row["max_attempts"],
            args=row["job_kwargs"] or {},
            status=row["status"],
            created_at=row["created_at"],
            last_run=row["last_run"],
            next_run=row["next_run"],
            run_count=row["run_count"] or 0,
            last_job_id=str(row["last_job_id"]) if row["last_job_id"] else None,
        )

    async def upsert(self, sched: _Schedule) -> None:
        # `job_name` has no UNIQUE constraint (PK is `id`), so we replace
        # any existing rows for this name in one transaction instead of
        # using ON CONFLICT. Keeps re-adds idempotent without a migration.
        async with self._conn() as conn:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM soniq_recurring_jobs WHERE job_name = $1",
                    sched.name,
                )
                # The pool's JSONB codec encodes the dict directly, so
                # don't json.dumps it here - that would double-encode.
                await conn.execute(
                    """
                    INSERT INTO soniq_recurring_jobs (
                        id, job_name, schedule_type, schedule_value, priority, queue,
                        max_attempts, job_kwargs, status, created_at, last_run, next_run,
                        run_count, updated_at
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, NOW())
                    """,
                    uuid.UUID(sched.id),
                    sched.name,
                    sched.schedule_type,
                    sched.schedule_value,
                    sched.priority,
                    sched.queue,
                    sched.max_attempts,
                    sched.args,
                    sched.status,
                    sched.created_at,
                    sched.last_run,
                    sched.next_run,
                    sched.run_count,
                )

    async def all(self) -> list[_Schedule]:
        async with self._conn() as conn:
            rows = await conn.fetch(
                """
                SELECT id, job_name, schedule_type, schedule_value, priority, queue,
                       max_attempts, job_kwargs, status, created_at, last_run, next_run,
                       run_count, last_job_id
                FROM soniq_recurring_jobs
                """
            )
        return [self._row_to_schedule(r) for r in rows]

    async def get(self, name: str) -> Optional[_Schedule]:
        async with self._conn() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, job_name, schedule_type, schedule_value, priority, queue,
                       max_attempts, job_kwargs, status, created_at, last_run, next_run,
                       run_count, last_job_id
                FROM soniq_recurring_jobs
                WHERE job_name = $1
                """,
                name,
            )
        return self._row_to_schedule(row) if row else None

    async def delete(self, name: str) -> bool:
        async with self._conn() as conn:
            result = await conn.execute(
                "DELETE FROM soniq_recurring_jobs WHERE job_name = $1", name
            )
        return rows_affected(result) > 0

    async def set_status(self, name: str, status: str) -> bool:
        async with self._conn() as conn:
            result = await conn.execute(
                """
                UPDATE soniq_recurring_jobs
                SET status = $1, updated_at = NOW()
                WHERE job_name = $2
                """,
                status,
                name,
            )
        return rows_affected(result) > 0


class Scheduler:
    """Per-app scheduler service. Created lazily on first `app.scheduler` access.

    Construction is cheap and synchronous: nothing touches the backend until
    `start()`, `add()`, or another async method runs. This keeps the lazy
    property semantics simple - importing `app.scheduler` from non-async
    code does not need an event loop.
    """

    def __init__(self, app: "Soniq") -> None:
        self._app = app
        self._store: Optional[Union[_SqlStore, _MemoryStore]] = None
        self._cache: dict[str, _Schedule] = {}
        self._loaded = False
        self._cache_lock = asyncio.Lock()
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._check_interval = 30

    # ------------------------------------------------------------------
    # Storage selection and cache maintenance
    # ------------------------------------------------------------------

    async def _get_store(self) -> Union[_SqlStore, _MemoryStore]:
        if self._store is not None:
            return self._store
        await self._app.ensure_initialized()
        backend = self._app.backend
        # Postgres is the only backend with the soniq_recurring_jobs table
        # and a real connection pool. Everything else (Memory, SQLite) keeps
        # schedules in-process: they are single-writer and have no DDL for
        # this feature.
        if isinstance(backend, PostgresBackend):
            self._store = _SqlStore(backend)
        else:
            self._store = _MemoryStore()
        return self._store

    async def _ensure_loaded(self) -> None:
        """Fail-closed cache hydrate.

        Builds the new cache locally and only swaps it in once every row
        parsed cleanly. If anything raises, `_loaded` stays False so the
        next call retries from scratch instead of publishing a half-built
        view.
        """
        if self._loaded:
            return
        async with self._cache_lock:
            if self._loaded:
                return
            store = await self._get_store()
            schedules = await store.all()
            now = datetime.now(timezone.utc)
            cache: dict[str, _Schedule] = {}
            for sched in schedules:
                if sched.next_run is None:
                    next_run = _calculate_next_run(
                        sched.schedule_type, sched.schedule_value, now
                    )
                    if next_run is not None:
                        sched.next_run = next_run
                        await store.upsert(sched)
                cache[sched.name] = sched
            self._cache = cache
            self._loaded = True

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def add(
        self,
        target: Any,
        *,
        cron: Any = None,
        every: Any = None,
        args: Optional[dict] = None,
        queue: Optional[str] = None,
        priority: Optional[int] = None,
        max_attempts: Optional[int] = None,
        name: Optional[str] = None,
    ) -> str:
        """Register or update a recurring schedule.

        `target` may be a callable registered with `@app.job` or a string
        task name. The schedule is keyed by the resolved task name; calling
        `add()` again with the same name updates the existing schedule
        rather than creating a duplicate. Returns the schedule's name.
        """
        pattern = self._app.settings.task_name_pattern
        if name is not None:
            job_name = validate_task_name(name, pattern)
        elif isinstance(target, str):
            job_name = validate_task_name(target, pattern)
        elif callable(target):
            job_name = (
                getattr(target, "_soniq_name", None)
                or f"{target.__module__}.{target.__name__}"
            )
        else:
            raise TypeError(
                "add(target=...) requires a callable, a task-name string, or name=..."
            )

        schedule_type, schedule_value = _coerce_schedule(cron=cron, every=every)
        args = args or {}
        try:
            json.dumps(args)
        except TypeError as exc:
            raise ValueError("args must be JSON-serializable") from exc

        # Pick up registered defaults when caller didn't override.
        meta = self._app.registry.get_job(job_name)
        if meta is not None:
            final_priority = priority if priority is not None else meta["priority"]
            final_queue = queue if queue is not None else meta["queue"]
            final_max_attempts = (
                max_attempts if max_attempts is not None else meta["max_retries"] + 1
            )
        else:
            final_priority = priority if priority is not None else 100
            final_queue = queue if queue is not None else "default"
            final_max_attempts = max_attempts if max_attempts is not None else 3

        now = datetime.now(timezone.utc)

        store = await self._get_store()
        await self._ensure_loaded()

        existing = self._cache.get(job_name)
        sched_id = existing.id if existing is not None else str(uuid.uuid4())
        next_run = _calculate_next_run(schedule_type, schedule_value, now)

        sched = _Schedule(
            id=sched_id,
            name=job_name,
            schedule_type=schedule_type,
            schedule_value=schedule_value,
            priority=final_priority,
            queue=final_queue,
            max_attempts=final_max_attempts,
            args=args,
            status=existing.status if existing is not None else "active",
            created_at=existing.created_at if existing is not None else now,
            last_run=existing.last_run if existing is not None else None,
            next_run=next_run,
            run_count=existing.run_count if existing is not None else 0,
            last_job_id=existing.last_job_id if existing is not None else None,
        )

        await store.upsert(sched)
        self._cache[job_name] = sched
        return job_name

    async def pause(self, name: str) -> bool:
        store = await self._get_store()
        await self._ensure_loaded()
        ok = await store.set_status(name, "paused")
        if ok and name in self._cache:
            self._cache[name].status = "paused"
        return ok

    async def resume(self, name: str) -> bool:
        store = await self._get_store()
        await self._ensure_loaded()
        ok = await store.set_status(name, "active")
        if ok and name in self._cache:
            self._cache[name].status = "active"
        return ok

    async def remove(self, name: str) -> bool:
        store = await self._get_store()
        await self._ensure_loaded()
        ok = await store.delete(name)
        if ok:
            self._cache.pop(name, None)
        return ok

    async def list(self, status: Optional[str] = None) -> list[dict]:
        await self._ensure_loaded()
        items = list(self._cache.values())
        if status is not None:
            items = [s for s in items if s.status == status]
        return [self._to_dict(s) for s in items]

    async def get(self, name: str) -> Optional[dict]:
        await self._ensure_loaded()
        sched = self._cache.get(name)
        return self._to_dict(sched) if sched is not None else None

    @staticmethod
    def _to_dict(s: _Schedule) -> dict:
        return {
            "id": s.id,
            "name": s.name,
            "schedule_type": s.schedule_type,
            "schedule_value": s.schedule_value,
            "priority": s.priority,
            "queue": s.queue,
            "max_attempts": s.max_attempts,
            "args": dict(s.args),
            "status": s.status,
            "created_at": s.created_at,
            "last_run": s.last_run,
            "next_run": s.next_run,
            "run_count": s.run_count,
            "last_job_id": s.last_job_id,
        }

    # ------------------------------------------------------------------
    # Scheduler loop
    # ------------------------------------------------------------------

    async def start(self, check_interval: int = 30) -> None:
        if self._running:
            return
        self._check_interval = max(1, int(check_interval))
        # Register any decorator-stamped @app.periodic functions before the
        # loop starts so a bare `soniq scheduler` process picks them up
        # without the user calling `add()` explicitly.
        await self._register_decorated()
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("Scheduler started (check interval: %ds)", self._check_interval)

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Scheduler stopped")

    @property
    def running(self) -> bool:
        return self._running

    async def _register_decorated(self) -> None:
        registry = self._app.registry
        for job_name, meta in registry.list_jobs().items():
            func = meta.get("func")
            spec = getattr(func, "_soniq_periodic", None)
            if not spec:
                continue
            await self.add(
                job_name,
                cron=spec.get("cron"),
                every=spec.get("every"),
                args=spec.get("args") or {},
                queue=spec.get("queue"),
                priority=spec.get("priority"),
                max_attempts=spec.get("max_attempts"),
                name=job_name,
            )

    async def _loop(self) -> None:
        """Tick loop guarded by the advisory-lock leader election.

        Only the leader for this tick scans for due jobs; everyone else
        sleeps. The per-job atomic claim inside `_execute_due` is the
        correctness floor regardless - leader election is an efficiency
        optimization on top.
        """
        while self._running:
            try:
                backend = self._app.backend
                async with with_advisory_lock(backend, "soniq.scheduler") as leader:
                    if leader:
                        await self._tick()
                await asyncio.sleep(self._check_interval)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Scheduler tick failed")
                await asyncio.sleep(min(self._check_interval, 60))

    async def _tick(self) -> None:
        await self._ensure_loaded()
        now = datetime.now(timezone.utc)
        # Snapshot to avoid mutating during iteration when execute updates
        # next_run on the same schedule.
        for sched in list(self._cache.values()):
            if sched.status != "active":
                continue
            if sched.next_run is None or now < sched.next_run:
                continue
            try:
                await self._execute_due(sched, now)
            except Exception:
                logger.exception("Failed to execute scheduled job %s", sched.name)

    async def _execute_due(self, sched: _Schedule, current_time: datetime) -> None:
        """Atomic claim + enqueue + bookkeeping.

        On Postgres all three steps run inside a single transaction so a
        failed enqueue rolls the claim back: a schedule cannot advance
        without producing an enqueued job, and a job cannot be enqueued
        without the schedule advancing. Multi-scheduler safety relies on
        the optimistic compare on `next_run`: only one scheduler observes
        rows_affected == 1 for a given tick.

        On non-Postgres backends the loop runs single-writer (no fleet),
        so the in-memory store provides the same compare-and-swap semantics
        without a transaction.
        """
        expected_next_run = sched.next_run
        if expected_next_run is None:
            return
        new_next_run = _calculate_next_run(
            sched.schedule_type, sched.schedule_value, current_time
        )
        if new_next_run is None:
            return

        store = await self._get_store()
        backend = self._app.backend

        if isinstance(store, _SqlStore):
            new_run_count = sched.run_count + 1
            actual_job_id: Optional[str] = None
            async with backend.acquire() as conn:
                async with conn.transaction():
                    claim = await conn.execute(
                        """
                        UPDATE soniq_recurring_jobs
                        SET next_run = $1, updated_at = NOW()
                        WHERE job_name = $2 AND next_run = $3
                        """,
                        new_next_run,
                        sched.name,
                        expected_next_run,
                    )
                    if rows_affected(claim) != 1:
                        # Another scheduler already advanced this one; sync
                        # our cache to the authoritative row and bail.
                        row = await conn.fetchrow(
                            "SELECT next_run FROM soniq_recurring_jobs WHERE job_name = $1",
                            sched.name,
                        )
                        if row and row["next_run"]:
                            sched.next_run = row["next_run"]
                        return

                    actual_job_id = await self._app.enqueue(
                        sched.name,
                        args=sched.args,
                        connection=conn,
                        priority=sched.priority,
                        queue=sched.queue,
                    )
                    await conn.execute(
                        """
                        UPDATE soniq_recurring_jobs
                        SET last_run = $1, run_count = $2, last_job_id = $3,
                            updated_at = NOW()
                        WHERE job_name = $4
                        """,
                        current_time,
                        new_run_count,
                        uuid.UUID(actual_job_id) if actual_job_id else None,
                        sched.name,
                    )
            sched.last_run = current_time
            sched.run_count = new_run_count
            sched.last_job_id = actual_job_id
            sched.next_run = new_next_run
            return

        # In-process store path. claim_and_advance is the CAS; if it fails
        # someone else (a stale duplicate Scheduler in tests, mainly) ran it.
        claimed = await store.claim_and_advance(
            sched.name, expected_next_run, new_next_run
        )
        if not claimed:
            updated = await store.get(sched.name)
            if updated is not None:
                sched.next_run = updated.next_run
            return

        actual_job_id = await self._app.enqueue(
            sched.name,
            args=sched.args,
            priority=sched.priority,
            queue=sched.queue,
        )
        new_run_count = sched.run_count + 1
        await store.record_run(
            sched.name,
            last_run=current_time,
            run_count=new_run_count,
            last_job_id=actual_job_id,
        )
        sched.last_run = current_time
        sched.run_count = new_run_count
        sched.last_job_id = actual_job_id
        sched.next_run = new_next_run
