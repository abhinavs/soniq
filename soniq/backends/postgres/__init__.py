"""PostgreSQL storage backend. Uses asyncpg with FOR UPDATE SKIP LOCKED, pg_notify, and transactional enqueue."""

import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Optional

import asyncpg

from soniq.backends.helpers import rows_affected as _rows_affected
from soniq.settings import get_settings
from soniq.types import QueueStats

from ...core.leadership import advisory_key

logger = logging.getLogger(__name__)


async def _init_connection(conn: asyncpg.Connection) -> None:
    """Set UTC timezone and register a JSONB codec so args/result are native Python dicts, not raw strings."""
    await conn.execute("SET timezone = 'UTC'")
    await conn.set_type_codec(
        "jsonb",
        encoder=lambda v: json.dumps(v, default=str),
        decoder=json.loads,
        schema="pg_catalog",
    )


def _row_to_dict(row: asyncpg.Record) -> dict:
    """Convert asyncpg Record to a plain dict with string IDs and ISO timestamps."""
    d: dict[str, Any] = {}
    for key in row.keys():
        val = row[key]
        if isinstance(val, uuid.UUID):
            d[key] = str(val)
        elif isinstance(val, datetime):
            d[key] = val.isoformat()
        else:
            d[key] = val
    return d


def _job_row_to_dict(row: asyncpg.Record) -> dict:
    """Convert a job row to the standard dict format. JSONB codec already decoded args/result."""
    return {
        "id": str(row["id"]),
        "job_name": row["job_name"],
        "args": row["args"],
        "status": row["status"],
        "attempts": row["attempts"],
        "max_attempts": row["max_attempts"],
        "queue": row["queue"],
        "priority": row["priority"],
        "scheduled_at": (
            row["scheduled_at"].isoformat() if row["scheduled_at"] else None
        ),
        "last_error": row["last_error"],
        "result": row["result"] if "result" in row.keys() else None,
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
    }


class PostgresBackend:

    def __init__(
        self,
        database_url: str,
        pool_min_size: int = 5,
        pool_max_size: int = 20,
    ):
        self._url = database_url
        self._pool_min = pool_min_size
        self._pool_max = pool_max_size
        self._pool: Optional[asyncpg.Pool] = None

    @staticmethod
    def _should_skip_lock() -> bool:
        env_val = os.environ.get("SONIQ_SKIP_UPDATE_LOCK", "").lower()
        if env_val not in {"1", "true", "yes", "on"}:
            return False

        settings = get_settings()
        return settings.debug or settings.environment == "testing"

    @property
    def supports_push_notify(self) -> bool:
        return True

    @property
    def supports_transactional_enqueue(self) -> bool:
        return True

    @property
    def supports_advisory_locks(self) -> bool:
        return True

    async def initialize(self) -> None:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                self._url,
                min_size=self._pool_min,
                max_size=self._pool_max,
                init=_init_connection,
            )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[asyncpg.Connection]:
        """Lend a pooled connection scoped to the ``async with`` block.

        The pool itself is private. Callers that need raw SQL outside the
        ``StorageBackend`` method surface (migration runner, dashboard
        data, listener cleanup) go through this contract. The connection
        is released on exit, so forgetting to release is impossible.
        """
        if self._pool is None:
            raise RuntimeError(
                "PostgresBackend not initialized. Call initialize() first."
            )
        async with self._pool.acquire() as conn:
            yield conn

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
        async with self.acquire() as conn:
            return await self._create_job_on_conn(
                conn,
                job_id=job_id,
                job_name=job_name,
                args=args,
                args_hash=args_hash,
                max_attempts=max_attempts,
                priority=priority,
                queue=queue,
                unique=unique,
                dedup_key=dedup_key,
                scheduled_at=scheduled_at,
                producer_id=producer_id,
            )

    async def create_job_transactional(
        self,
        *,
        connection: asyncpg.Connection,
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
        """Enqueue within a caller-provided transaction. PostgreSQL only."""
        return await self._create_job_on_conn(
            connection,
            job_id=job_id,
            job_name=job_name,
            args=args,
            args_hash=args_hash,
            max_attempts=max_attempts,
            priority=priority,
            queue=queue,
            unique=unique,
            dedup_key=dedup_key,
            scheduled_at=scheduled_at,
            producer_id=producer_id,
        )

    async def _create_job_on_conn(
        self,
        conn: asyncpg.Connection,
        *,
        job_id: str,
        job_name: str,
        args: dict,
        args_hash: Optional[str],
        max_attempts: int,
        priority: int,
        queue: str,
        unique: bool,
        dedup_key: Optional[str],
        scheduled_at: Optional[datetime],
        producer_id: Optional[str] = None,
    ) -> Optional[str]:
        uid = uuid.UUID(job_id)

        # ON CONFLICT DO UPDATE SET (no-op) RETURNING id fires for the existing row too,
        # so the returned id is always a real row - no caller-id can leak through a race window.
        if dedup_key:
            row = await conn.fetchrow(
                """
                INSERT INTO soniq_jobs
                    (id, job_name, args, args_hash, max_attempts, priority, queue,
                     unique_job, dedup_key, scheduled_at, producer_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                ON CONFLICT (dedup_key)
                    WHERE status = 'queued' AND dedup_key IS NOT NULL
                DO UPDATE SET dedup_key = EXCLUDED.dedup_key
                RETURNING id
                """,
                uid,
                job_name,
                args,
                args_hash,
                max_attempts,
                priority,
                queue,
                unique,
                dedup_key,
                scheduled_at,
                producer_id,
            )
            assert row is not None
            await conn.execute(
                "SELECT pg_notify($1, $2)",
                "soniq_new_job",
                queue,
            )
            return str(row["id"])

        if unique:
            row = await conn.fetchrow(
                """
                INSERT INTO soniq_jobs
                    (id, job_name, args, args_hash, max_attempts, priority, queue,
                     unique_job, scheduled_at, producer_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                ON CONFLICT (job_name, args_hash)
                    WHERE status = 'queued' AND unique_job = TRUE
                DO UPDATE SET unique_job = EXCLUDED.unique_job
                RETURNING id
                """,
                uid,
                job_name,
                args,
                args_hash,
                max_attempts,
                priority,
                queue,
                True,
                scheduled_at,
                producer_id,
            )
            assert row is not None
            await conn.execute(
                "SELECT pg_notify($1, $2)",
                "soniq_new_job",
                queue,
            )
            return str(row["id"])

        await conn.execute(
            """
            INSERT INTO soniq_jobs
                (id, job_name, args, args_hash, max_attempts, priority, queue,
                 unique_job, scheduled_at, producer_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """,
            uid,
            job_name,
            args,
            args_hash,
            max_attempts,
            priority,
            queue,
            unique,
            scheduled_at,
            producer_id,
        )
        await conn.execute(
            "SELECT pg_notify($1, $2)",
            "soniq_new_job",
            queue,
        )
        return job_id

    async def create_jobs_bulk(
        self,
        *,
        job_ids: list[str],
        job_name: str,
        args_list: list[dict],
        max_attempts: int,
        priority: int,
        queue: str,
        scheduled_at: Optional[datetime] = None,
        producer_id: Optional[str] = None,
    ) -> None:
        """Bulk-insert N jobs sharing the same target/queue/priority in a single round trip.

        Does not honour unique_job or dedup_key - the caller (``Soniq.enqueue_many``)
        rejects unique tasks before reaching this path.
        """
        if not job_ids:
            return
        rows = [
            (
                uuid.UUID(jid),
                job_name,
                args,
                None,
                max_attempts,
                priority,
                queue,
                False,
                scheduled_at,
                producer_id,
            )
            for jid, args in zip(job_ids, args_list)
        ]
        async with self.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO soniq_jobs
                    (id, job_name, args, args_hash, max_attempts, priority, queue,
                     unique_job, scheduled_at, producer_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                """,
                rows,
            )

    async def notify_new_job(self, queue: str) -> None:
        async with self.acquire() as conn:
            await conn.execute(
                "SELECT pg_notify($1, $2)",
                "soniq_new_job",
                queue,
            )

    async def listen_for_jobs(
        self,
        callback: Any,
        channel: str = "soniq_new_job",
    ) -> "_PostgresListenerHandle":
        """Start listening. Returns a handle whose ``close()`` removes the
        listener and releases the pinned connection back to the pool."""
        if self._pool is None:
            raise RuntimeError(
                "PostgresBackend not initialized. Call initialize() first."
            )
        conn = await self._pool.acquire()
        await conn.add_listener(channel, callback)
        return _PostgresListenerHandle(self._pool, conn, channel, callback)

    async def fetch_and_lock_job(
        self,
        *,
        queues: Optional[list[str]] = None,
        worker_id: Optional[str] = None,
    ) -> Optional[dict]:
        skip_lock = self._should_skip_lock()

        lock_clause = "" if skip_lock else "FOR UPDATE SKIP LOCKED"

        async with self.acquire() as conn:
            async with conn.transaction():
                if queues is None:
                    job_record = await conn.fetchrow(
                        f"""
                        SELECT * FROM soniq_jobs
                        WHERE status = 'queued'
                          AND (scheduled_at IS NULL OR scheduled_at <= NOW())
                        ORDER BY priority ASC, scheduled_at ASC NULLS FIRST, created_at ASC
                        {lock_clause}
                        LIMIT 1
                        """
                    )
                elif len(queues) == 1:
                    job_record = await conn.fetchrow(
                        f"""
                        SELECT * FROM soniq_jobs
                        WHERE status = 'queued'
                          AND queue = $1
                          AND (scheduled_at IS NULL OR scheduled_at <= NOW())
                        ORDER BY priority ASC, scheduled_at ASC NULLS FIRST, created_at ASC
                        {lock_clause}
                        LIMIT 1
                        """,
                        queues[0],
                    )
                else:
                    job_record = await conn.fetchrow(
                        f"""
                        SELECT * FROM soniq_jobs
                        WHERE status = 'queued'
                          AND queue = ANY($1)
                          AND (scheduled_at IS NULL OR scheduled_at <= NOW())
                        ORDER BY priority ASC, scheduled_at ASC NULLS FIRST, created_at ASC
                        {lock_clause}
                        LIMIT 1
                        """,
                        queues,
                    )

                if not job_record:
                    return None

                job_id = job_record["id"]
                if worker_id:
                    await conn.execute(
                        """
                        UPDATE soniq_jobs
                        SET status = 'processing', attempts = attempts + 1, worker_id = $2, updated_at = NOW()
                        WHERE id = $1
                        """,
                        job_id,
                        uuid.UUID(worker_id),
                    )
                else:
                    await conn.execute(
                        """
                        UPDATE soniq_jobs
                        SET status = 'processing', attempts = attempts + 1, updated_at = NOW()
                        WHERE id = $1
                        """,
                        job_id,
                    )

            result = dict(job_record)
            result["attempts"] = result["attempts"] + 1
            return result

    async def mark_job_done(
        self,
        job_id: str,
        *,
        result_ttl: Optional[int] = None,
        result: Any = None,
    ) -> None:
        uid = uuid.UUID(job_id)
        # Skip the result column write when the job returned None - most jobs are -> None.
        async with self.acquire() as conn:
            async with conn.transaction():
                if result_ttl is not None and result_ttl == 0:
                    await conn.execute(
                        "DELETE FROM soniq_jobs WHERE id = $1",
                        uid,
                    )
                elif result is None:
                    ttl = result_ttl if result_ttl is not None else 3600
                    await conn.execute(
                        """
                        UPDATE soniq_jobs
                        SET status = 'done',
                            expires_at = NOW() + ($2 || ' seconds')::INTERVAL,
                            updated_at = NOW()
                        WHERE id = $1
                        """,
                        uid,
                        str(ttl),
                    )
                else:
                    ttl = result_ttl if result_ttl is not None else 3600
                    await conn.execute(
                        """
                        UPDATE soniq_jobs
                        SET status = 'done',
                            result = $3,
                            expires_at = NOW() + ($2 || ' seconds')::INTERVAL,
                            updated_at = NOW()
                        WHERE id = $1
                        """,
                        uid,
                        str(ttl),
                        result,
                    )

    async def mark_job_failed(
        self,
        job_id: str,
        *,
        attempts: int,
        error: str,
        retry_delay: Optional[float] = None,
    ) -> None:
        uid = uuid.UUID(job_id)
        async with self.acquire() as conn:
            async with conn.transaction():
                if retry_delay and retry_delay > 0:
                    await conn.execute(
                        """
                        UPDATE soniq_jobs
                        SET status = 'queued',
                            attempts = $1,
                            last_error = $2,
                            scheduled_at = NOW() + ($3 || ' seconds')::INTERVAL,
                            updated_at = NOW()
                        WHERE id = $4
                        """,
                        attempts,
                        error,
                        str(retry_delay),
                        uid,
                    )
                else:
                    await conn.execute(
                        """
                        UPDATE soniq_jobs
                        SET status = 'queued',
                            attempts = $1,
                            last_error = $2,
                            scheduled_at = NULL,
                            updated_at = NOW()
                        WHERE id = $3
                        """,
                        attempts,
                        error,
                        uid,
                    )

    async def mark_job_dead_letter(
        self,
        job_id: str,
        *,
        attempts: int,
        error: str,
        reason: str,
        tags: Optional[dict] = None,
    ) -> None:
        # DLQ Option A: move source row to soniq_dead_letter_jobs in one transaction.
        # SELECT FOR UPDATE prevents concurrent cancel/recovery from racing with the move.
        uid = uuid.UUID(job_id)
        tags_json = json.dumps(tags) if tags is not None else None
        async with self.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT * FROM soniq_jobs WHERE id = $1 FOR UPDATE",
                    uid,
                )
                if row is None:
                    return
                await conn.execute(
                    """
                    INSERT INTO soniq_dead_letter_jobs (
                        id, job_name, args, queue, priority, max_attempts,
                        attempts, last_error, dead_letter_reason,
                        original_created_at, moved_to_dead_letter_at, tags
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6,
                        $7, $8, $9,
                        $10, NOW(), $11
                    )
                    """,
                    uid,
                    row["job_name"],
                    row["args"],
                    row["queue"],
                    row["priority"],
                    row["max_attempts"],
                    attempts,
                    error,
                    reason,
                    row["created_at"],
                    tags_json,
                )
                await conn.execute(
                    "DELETE FROM soniq_jobs WHERE id = $1",
                    uid,
                )

    async def nack_job(self, job_id: str) -> None:
        uid = uuid.UUID(job_id)
        async with self.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    UPDATE soniq_jobs
                    SET status = 'queued',
                        worker_id = NULL,
                        scheduled_at = NOW(),
                        updated_at = NOW()
                    WHERE id = $1 AND status = 'processing'
                    """,
                    uid,
                )

    async def reschedule_job(
        self,
        job_id: str,
        *,
        delay_seconds: float,
        attempts: int,
        reason: Optional[str] = None,
    ) -> None:
        uid = uuid.UUID(job_id)
        # SNOOZE: prefix in last_error lets tooling distinguish snoozes from real failures without a schema change.
        reason_text = f"SNOOZE: {reason}" if reason else "SNOOZE"
        async with self.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    UPDATE soniq_jobs
                    SET status = 'queued',
                        attempts = $1,
                        scheduled_at = NOW() + ($2 || ' seconds')::INTERVAL,
                        last_error = $3,
                        updated_at = NOW()
                    WHERE id = $4
                    """,
                    attempts,
                    str(delay_seconds),
                    reason_text,
                    uid,
                )

    async def cancel_job(self, job_id: str) -> bool:
        uid = uuid.UUID(job_id)
        async with self.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE soniq_jobs
                SET status = 'cancelled', updated_at = NOW()
                WHERE id = $1 AND status = 'queued'
                """,
                uid,
            )
            return _rows_affected(result) == 1

    async def delete_job(self, job_id: str) -> bool:
        uid = uuid.UUID(job_id)
        async with self.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM soniq_jobs WHERE id = $1",
                uid,
            )
            return _rows_affected(result) == 1

    async def get_job(self, job_id: str) -> Optional[dict]:
        uid = uuid.UUID(job_id)
        async with self.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, job_name, args, status, attempts, max_attempts,
                       queue, priority, scheduled_at, last_error, result,
                       created_at, updated_at
                FROM soniq_jobs
                WHERE id = $1
                """,
                uid,
            )
            if not row:
                return None
            return _job_row_to_dict(row)

    async def list_jobs(
        self,
        *,
        queue: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        conditions: list[str] = []
        params: list[Any] = []
        param_count = 0

        if queue is not None:
            param_count += 1
            conditions.append(f"queue = ${param_count}")
            params.append(queue)

        if status is not None:
            param_count += 1
            conditions.append(f"status = ${param_count}")
            params.append(status)

        where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""

        param_count += 1
        params.append(limit)
        limit_param = f"${param_count}"

        param_count += 1
        params.append(offset)
        offset_param = f"${param_count}"

        query = f"""
            SELECT id, job_name, args, status, attempts, max_attempts,
                   queue, priority, scheduled_at, last_error,
                   created_at, updated_at
            FROM soniq_jobs
            {where_clause}
            ORDER BY created_at DESC
            LIMIT {limit_param} OFFSET {offset_param}
        """

        async with self.acquire() as conn:
            rows = await conn.fetch(query, *params)
            return [_job_row_to_dict(row) for row in rows]

    async def get_queue_stats(self) -> QueueStats:
        # Two queries in one acquire to keep the count snapshot tight.
        async with self.acquire() as conn:
            jobs_row = await conn.fetchrow(
                """
                SELECT
                    COUNT(*) FILTER (WHERE status = 'queued')     AS queued,
                    COUNT(*) FILTER (WHERE status = 'processing') AS processing,
                    COUNT(*) FILTER (WHERE status = 'done')       AS done,
                    COUNT(*) FILTER (WHERE status = 'cancelled')  AS cancelled
                FROM soniq_jobs
                """
            )
            dlq_count = await conn.fetchval(
                "SELECT COUNT(*) FROM soniq_dead_letter_jobs"
            )
        queued = int(jobs_row["queued"] or 0)
        processing = int(jobs_row["processing"] or 0)
        done = int(jobs_row["done"] or 0)
        cancelled = int(jobs_row["cancelled"] or 0)
        dead_letter = int(dlq_count or 0)
        return QueueStats(
            total=queued + processing + done + cancelled + dead_letter,
            queued=queued,
            processing=processing,
            done=done,
            dead_letter=dead_letter,
            cancelled=cancelled,
        )

    async def register_task_name(
        self,
        *,
        task_name: str,
        worker_id: str,
        args_model_repr: Optional[str] = None,
    ) -> None:
        """Upsert this worker's registration for ``task_name``.

        Observability only: nothing in the enqueue path reads this table.
        See the boundary tests in tests/unit/test_enqueue.py.
        """
        async with self.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO soniq_task_registry
                    (task_name, worker_id, last_seen_at, args_model_repr)
                VALUES ($1, $2, NOW(), $3)
                ON CONFLICT (task_name, worker_id)
                DO UPDATE SET
                    last_seen_at = NOW(),
                    args_model_repr = EXCLUDED.args_model_repr
                """,
                task_name,
                worker_id,
                args_model_repr,
            )

    async def list_registered_task_names(self) -> list[dict]:
        """Return registered (task_name, worker_id, last_seen_at) rows.

        Observability only. See ``register_task_name``.
        """
        async with self.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT task_name, worker_id, last_seen_at, args_model_repr
                FROM soniq_task_registry
                ORDER BY task_name, worker_id
                """
            )
            return [dict(r) for r in rows]

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
        uid = uuid.UUID(worker_id)
        async with self.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO soniq_workers
                    (id, hostname, pid, queues, concurrency, status, started_at, metadata)
                VALUES ($1, $2, $3, $4, $5, 'active', NOW(), $6)
                ON CONFLICT (hostname, pid)
                DO UPDATE SET
                    id = EXCLUDED.id,
                    queues = EXCLUDED.queues,
                    concurrency = EXCLUDED.concurrency,
                    status = 'active',
                    last_heartbeat = NOW(),
                    started_at = NOW(),
                    metadata = EXCLUDED.metadata
                """,
                uid,
                hostname,
                pid,
                queues,
                concurrency,
                metadata,
            )

    async def update_heartbeat(
        self,
        worker_id: str,
        metadata: Optional[dict] = None,
    ) -> None:
        uid = uuid.UUID(worker_id)
        async with self.acquire() as conn:
            await conn.execute(
                """
                UPDATE soniq_workers
                SET last_heartbeat = NOW(), metadata = $2
                WHERE id = $1
                """,
                uid,
                metadata,
            )

    async def mark_worker_stopped(self, worker_id: str) -> None:
        uid = uuid.UUID(worker_id)
        async with self.acquire() as conn:
            await conn.execute(
                """
                UPDATE soniq_workers
                SET status = 'stopped', last_heartbeat = NOW()
                WHERE id = $1
                """,
                uid,
            )

    async def get_worker_status(self) -> dict:
        """Snapshot of registered workers for the CLI ``soniq inspect``
        command. Returns status counts, active workers with uptime, and
        any workers whose heartbeat is older than 5 minutes.
        """
        async with self.acquire() as conn:
            status_counts = await conn.fetch(
                "SELECT status, COUNT(*) as count FROM soniq_workers GROUP BY status"
            )

            active_workers = await conn.fetch(
                """
                SELECT id, hostname, pid, queues, concurrency, last_heartbeat,
                       started_at, metadata
                FROM soniq_workers
                WHERE status = 'active'
                ORDER BY last_heartbeat DESC
                """
            )

            stale_workers = await conn.fetch(
                """
                SELECT id, hostname, pid, last_heartbeat
                FROM soniq_workers
                WHERE status = 'active'
                  AND last_heartbeat < NOW() - INTERVAL '300 seconds'
                """
            )

        now = datetime.now(timezone.utc)
        return {
            "status_counts": {row["status"]: row["count"] for row in status_counts},
            "active_workers": [
                {
                    "id": str(row["id"]),
                    "hostname": row["hostname"],
                    "pid": row["pid"],
                    "queues": row["queues"] or [],
                    "concurrency": row["concurrency"],
                    "last_heartbeat": row["last_heartbeat"].isoformat(),
                    "started_at": row["started_at"].isoformat(),
                    "uptime_seconds": (
                        now
                        - (
                            row["started_at"].replace(tzinfo=timezone.utc)
                            if row["started_at"].tzinfo is None
                            else row["started_at"]
                        )
                    ).total_seconds(),
                    "metadata": row["metadata"],
                }
                for row in active_workers
            ],
            "stale_workers": [
                {
                    "id": str(row["id"]),
                    "hostname": row["hostname"],
                    "pid": row["pid"],
                    "last_heartbeat": row["last_heartbeat"].isoformat(),
                }
                for row in stale_workers
            ],
            "total_concurrency": sum(w["concurrency"] for w in active_workers),
            "health": "healthy" if not stale_workers else "degraded",
        }

    async def cleanup_stale_workers(
        self,
        stale_threshold_seconds: int,
    ) -> int:
        async with self.acquire() as conn:
            async with conn.transaction():
                stale_rows = await conn.fetch(
                    """
                    UPDATE soniq_workers
                    SET status = 'stopped'
                    WHERE status = 'active'
                      AND last_heartbeat < NOW() - ($1 || ' seconds')::INTERVAL
                    RETURNING id
                    """,
                    str(stale_threshold_seconds),
                )

                if not stale_rows:
                    return 0

                stale_ids = [row["id"] for row in stale_rows]
                await conn.execute(
                    """
                    UPDATE soniq_jobs
                    SET status = 'queued', worker_id = NULL, updated_at = NOW()
                    WHERE status = 'processing'
                      AND worker_id = ANY($1::uuid[])
                    """,
                    stale_ids,
                )

                return len(stale_ids)

    async def delete_expired_jobs(self) -> int:
        async with self.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM soniq_jobs WHERE status = 'done' AND expires_at < NOW()"
            )
            return _rows_affected(result)

    async def reset(self) -> None:
        async with self.acquire() as conn:
            await conn.execute("TRUNCATE soniq_jobs CASCADE")
            await conn.execute("TRUNCATE soniq_workers CASCADE")

    @asynccontextmanager
    async def with_advisory_lock(self, name: str) -> AsyncIterator[bool]:
        """Try a session-scoped advisory lock. Yields True if acquired, False if already held by another session."""
        key = advisory_key(name)
        async with self.acquire() as conn:
            acquired = await conn.fetchval("SELECT pg_try_advisory_lock($1)", key)
            try:
                yield bool(acquired)
            finally:
                if acquired:
                    try:
                        await conn.fetchval("SELECT pg_advisory_unlock($1)", key)
                    except Exception as e:
                        logger.warning(
                            "Failed to release advisory lock %r: %s", name, e
                        )


class _PostgresListenerHandle:
    """LISTEN handle backed by a dedicated connection. close() removes the listener and releases the connection."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        conn: asyncpg.Connection,
        channel: str,
        callback: Any,
    ) -> None:
        self._pool = pool
        self._conn = conn
        self._channel = channel
        self._callback = callback

    async def close(self) -> None:
        try:
            await self._conn.remove_listener(self._channel, self._callback)
        except Exception:
            logger.debug(
                "remove_listener failed during listener shutdown", exc_info=True
            )
        try:
            await self._pool.release(self._conn)
        except Exception:
            logger.debug("pool.release failed during listener shutdown", exc_info=True)
