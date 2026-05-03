"""SQLite storage backend. Zero-setup local dev, single-writer, polling only."""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from soniq.types import QueueStats

logger = logging.getLogger(__name__)

try:
    import aiosqlite
except ImportError:
    aiosqlite = None  # type: ignore[assignment]


def _require_aiosqlite() -> None:
    if aiosqlite is None:
        raise ImportError(
            "aiosqlite is required for the SQLite backend. "
            "Install it with: pip install soniq[sqlite]"
        )


class SQLiteBackend:

    def __init__(self, path: str = "soniq.db") -> None:
        _require_aiosqlite()
        self._path = path
        self._conn: Optional[aiosqlite.Connection] = None

    @property
    def supports_push_notify(self) -> bool:
        return False

    @property
    def supports_transactional_enqueue(self) -> bool:
        return False

    @property
    def supports_advisory_locks(self) -> bool:
        return False

    async def initialize(self) -> None:
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self._create_tables()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def _create_tables(self) -> None:
        assert self._conn is not None
        await self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS soniq_jobs (
                id TEXT PRIMARY KEY,
                job_name TEXT NOT NULL,
                args TEXT NOT NULL,
                args_hash TEXT,
                status TEXT NOT NULL DEFAULT 'queued'
                    CHECK (status IN ('queued', 'processing', 'done', 'cancelled')),
                attempts INTEGER DEFAULT 0,
                max_attempts INTEGER DEFAULT 3,
                queue TEXT DEFAULT 'default',
                priority INTEGER DEFAULT 100,
                unique_job INTEGER DEFAULT 0,
                dedup_key TEXT,
                scheduled_at TEXT,
                expires_at TEXT,
                last_error TEXT,
                result TEXT,
                worker_id TEXT,
                producer_id TEXT,
                created_at TEXT,
                updated_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_eq_jobs_status_priority
                ON soniq_jobs (status, priority) WHERE status = 'queued';
            CREATE INDEX IF NOT EXISTS idx_eq_jobs_queue_status
                ON soniq_jobs (queue, status);

            -- BEFORE-trigger mirrors the postgres CHECK: surfaces the DLQ contract violation
            -- even if a future migration changes the column-level CHECK shape.
            CREATE TRIGGER IF NOT EXISTS soniq_jobs_reject_dead_letter_insert
            BEFORE INSERT ON soniq_jobs
            FOR EACH ROW WHEN NEW.status = 'dead_letter'
            BEGIN
                SELECT RAISE(ABORT, 'soniq_jobs.status=dead_letter is rejected: DLQ rows live in soniq_dead_letter_jobs');
            END;

            CREATE TRIGGER IF NOT EXISTS soniq_jobs_reject_dead_letter_update
            BEFORE UPDATE ON soniq_jobs
            FOR EACH ROW WHEN NEW.status = 'dead_letter'
            BEGIN
                SELECT RAISE(ABORT, 'soniq_jobs.status=dead_letter is rejected: DLQ rows live in soniq_dead_letter_jobs');
            END;

            CREATE TABLE IF NOT EXISTS soniq_workers (
                id TEXT PRIMARY KEY,
                hostname TEXT NOT NULL,
                pid INTEGER NOT NULL,
                queues TEXT DEFAULT '[]',
                concurrency INTEGER DEFAULT 1,
                status TEXT DEFAULT 'active',
                last_heartbeat TEXT,
                started_at TEXT,
                metadata TEXT
            );

            CREATE TABLE IF NOT EXISTS soniq_dead_letter_jobs (
                id TEXT PRIMARY KEY,
                job_name TEXT NOT NULL,
                args TEXT NOT NULL,
                queue TEXT NOT NULL,
                priority INTEGER NOT NULL,
                max_attempts INTEGER NOT NULL,
                attempts INTEGER NOT NULL,
                last_error TEXT,
                dead_letter_reason TEXT NOT NULL,
                original_created_at TEXT NOT NULL,
                moved_to_dead_letter_at TEXT NOT NULL,
                resurrection_count INTEGER DEFAULT 0,
                last_resurrection_at TEXT,
                tags TEXT,
                created_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_soniq_dead_letter_jobs_job_name
                ON soniq_dead_letter_jobs (job_name);
            CREATE INDEX IF NOT EXISTS idx_soniq_dead_letter_jobs_queue
                ON soniq_dead_letter_jobs (queue);
            CREATE INDEX IF NOT EXISTS idx_soniq_dead_letter_jobs_reason
                ON soniq_dead_letter_jobs (dead_letter_reason);
            CREATE INDEX IF NOT EXISTS idx_soniq_dead_letter_jobs_moved_at
                ON soniq_dead_letter_jobs (moved_to_dead_letter_at);
            """
        )

        # Upgrade existing databases missing columns added after initial release.
        cursor = await self._conn.execute("PRAGMA table_info(soniq_jobs)")
        columns = {row[1] for row in await cursor.fetchall()}
        if "result" not in columns:
            await self._conn.execute("ALTER TABLE soniq_jobs ADD COLUMN result TEXT")
        if "producer_id" not in columns:
            await self._conn.execute(
                "ALTER TABLE soniq_jobs ADD COLUMN producer_id TEXT"
            )

        await self._conn.commit()

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
        assert self._conn is not None
        now = _now_iso()

        if unique and args_hash:
            async with self._conn.execute(
                """
                SELECT id FROM soniq_jobs
                WHERE job_name = ? AND args_hash = ? AND status = 'queued' AND unique_job = 1
                """,
                (job_name, args_hash),
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return str(row["id"])

        if dedup_key:
            async with self._conn.execute(
                "SELECT id FROM soniq_jobs WHERE dedup_key = ? AND status = 'queued'",
                (dedup_key,),
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return str(row["id"])

        sched = scheduled_at.isoformat() if scheduled_at else None
        args_serialized = json.dumps(args, default=str)
        await self._conn.execute(
            """
            INSERT INTO soniq_jobs
                (id, job_name, args, args_hash, max_attempts, priority, queue,
                 unique_job, dedup_key, scheduled_at, producer_id, created_at,
                 updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                job_name,
                args_serialized,
                args_hash,
                max_attempts,
                priority,
                queue,
                1 if unique else 0,
                dedup_key,
                sched,
                producer_id,
                now,
                now,
            ),
        )
        await self._conn.commit()
        return job_id

    async def fetch_and_lock_job(
        self,
        *,
        queues: Optional[list[str]] = None,
        worker_id: Optional[str] = None,
    ) -> Optional[dict]:
        assert self._conn is not None
        now = _now_iso()

        if queues:
            placeholders = ",".join("?" for _ in queues)
            query = f"""
                SELECT * FROM soniq_jobs
                WHERE status = 'queued'
                  AND queue IN ({placeholders})
                  AND (scheduled_at IS NULL OR scheduled_at <= ?)
                ORDER BY priority ASC, scheduled_at ASC, created_at ASC
                LIMIT 1
            """
            params: tuple = (*queues, now)
        else:
            query = """
                SELECT * FROM soniq_jobs
                WHERE status = 'queued'
                  AND (scheduled_at IS NULL OR scheduled_at <= ?)
                ORDER BY priority ASC, scheduled_at ASC, created_at ASC
                LIMIT 1
            """
            params = (now,)

        async with self._conn.execute(query, params) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None

            job_id = row["id"]
            await self._conn.execute(
                "UPDATE soniq_jobs SET status='processing', attempts=attempts+1, worker_id=?, updated_at=? WHERE id=?",
                (worker_id, _now_iso(), job_id),
            )
            await self._conn.commit()
            result = _sqlite_row_to_dict(row)
            result["attempts"] = result["attempts"] + 1
            return result

    async def notify_new_job(self, queue: str) -> None:
        pass  # No push notification

    async def listen_for_jobs(
        self, callback: Any, channel: str = "soniq_new_job"
    ) -> None:
        pass  # No push notification

    async def mark_job_done(
        self, job_id: str, *, result_ttl: Optional[int] = None, result: Any = None
    ) -> None:
        assert self._conn is not None
        if result_ttl is not None and result_ttl == 0:
            await self._conn.execute("DELETE FROM soniq_jobs WHERE id=?", (job_id,))
        else:
            ttl = result_ttl if result_ttl is not None else 3600
            expires = (datetime.now(timezone.utc) + timedelta(seconds=ttl)).isoformat()
            if result is None:
                await self._conn.execute(
                    "UPDATE soniq_jobs SET status='done', expires_at=?, updated_at=? WHERE id=?",
                    (expires, _now_iso(), job_id),
                )
            else:
                result_json = json.dumps(result, default=str)
                await self._conn.execute(
                    "UPDATE soniq_jobs SET status='done', result=?, expires_at=?, updated_at=? WHERE id=?",
                    (result_json, expires, _now_iso(), job_id),
                )
        await self._conn.commit()

    async def mark_job_failed(
        self,
        job_id: str,
        *,
        attempts: int,
        error: str,
        retry_delay: Optional[float] = None,
    ) -> None:
        assert self._conn is not None
        if retry_delay and retry_delay > 0:
            sched = (
                datetime.now(timezone.utc) + timedelta(seconds=retry_delay)
            ).isoformat()
            await self._conn.execute(
                "UPDATE soniq_jobs SET status='queued', attempts=?, last_error=?, scheduled_at=?, updated_at=? WHERE id=?",
                (attempts, error, sched, _now_iso(), job_id),
            )
        else:
            await self._conn.execute(
                "UPDATE soniq_jobs SET status='queued', attempts=?, last_error=?, scheduled_at=NULL, updated_at=? WHERE id=?",
                (attempts, error, _now_iso(), job_id),
            )
        await self._conn.commit()

    async def mark_job_dead_letter(
        self,
        job_id: str,
        *,
        attempts: int,
        error: str,
        reason: str,
        tags: Optional[dict] = None,
    ) -> None:
        # DLQ Option A: move row to soniq_dead_letter_jobs. aiosqlite's implicit transaction
        # keeps both tables consistent on crash; the Python lock on self._conn prevents races.
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT * FROM soniq_jobs WHERE id=?",
            (job_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return
        now = _now_iso()
        tags_json = json.dumps(tags) if tags is not None else None
        await self._conn.execute(
            """
            INSERT INTO soniq_dead_letter_jobs (
                id, job_name, args, queue, priority, max_attempts,
                attempts, last_error, dead_letter_reason,
                original_created_at, moved_to_dead_letter_at,
                resurrection_count, tags, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            """,
            (
                job_id,
                row["job_name"],
                row["args"],
                row["queue"],
                row["priority"],
                row["max_attempts"],
                attempts,
                error,
                reason,
                row["created_at"],
                now,
                tags_json,
                now,
            ),
        )
        await self._conn.execute(
            "DELETE FROM soniq_jobs WHERE id=?",
            (job_id,),
        )
        await self._conn.commit()

    async def nack_job(self, job_id: str) -> None:
        assert self._conn is not None
        now = _now_iso()
        await self._conn.execute(
            "UPDATE soniq_jobs SET status='queued', worker_id=NULL, scheduled_at=?, updated_at=? "
            "WHERE id=? AND status='processing'",
            (now, now, job_id),
        )
        await self._conn.commit()

    async def reschedule_job(
        self,
        job_id: str,
        *,
        delay_seconds: float,
        attempts: int,
        reason: Optional[str] = None,
    ) -> None:
        assert self._conn is not None
        sched = (
            datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
        ).isoformat()
        reason_text = f"SNOOZE: {reason}" if reason else "SNOOZE"
        await self._conn.execute(
            "UPDATE soniq_jobs SET status='queued', attempts=?, scheduled_at=?, last_error=?, updated_at=? WHERE id=?",
            (attempts, sched, reason_text, _now_iso(), job_id),
        )
        await self._conn.commit()

    async def cancel_job(self, job_id: str) -> bool:
        assert self._conn is not None
        cursor = await self._conn.execute(
            "UPDATE soniq_jobs SET status='cancelled', updated_at=? WHERE id=? AND status='queued'",
            (_now_iso(), job_id),
        )
        await self._conn.commit()
        return bool(cursor.rowcount and cursor.rowcount > 0)

    async def delete_job(self, job_id: str) -> bool:
        assert self._conn is not None
        cursor = await self._conn.execute(
            "DELETE FROM soniq_jobs WHERE id=?",
            (job_id,),
        )
        await self._conn.commit()
        return bool(cursor.rowcount and cursor.rowcount > 0)

    # --- Queries ---

    async def get_job(self, job_id: str) -> Optional[dict]:
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT * FROM soniq_jobs WHERE id=?",
            (job_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return _sqlite_row_to_dict(row)

    async def list_jobs(
        self,
        *,
        queue: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        assert self._conn is not None
        conditions = []
        params: list[Any] = []
        if queue:
            conditions.append("queue=?")
            params.append(queue)
        if status:
            conditions.append("status=?")
            params.append(status)
        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        params.extend([limit, offset])

        async with self._conn.execute(
            f"SELECT * FROM soniq_jobs {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params,
        ) as cursor:
            rows = await cursor.fetchall()
            return [_sqlite_row_to_dict(r) for r in rows]

    async def get_queue_stats(self) -> QueueStats:
        assert self._conn is not None
        async with self._conn.execute(
            """
            SELECT
                SUM(CASE WHEN status='queued'     THEN 1 ELSE 0 END) AS queued,
                SUM(CASE WHEN status='processing' THEN 1 ELSE 0 END) AS processing,
                SUM(CASE WHEN status='done'       THEN 1 ELSE 0 END) AS done,
                SUM(CASE WHEN status='cancelled'  THEN 1 ELSE 0 END) AS cancelled
            FROM soniq_jobs
            """
        ) as cursor:
            row = await cursor.fetchone()
        async with self._conn.execute(
            "SELECT COUNT(*) AS c FROM soniq_dead_letter_jobs"
        ) as cursor:
            dlq_row = await cursor.fetchone()
        queued = int(row["queued"] or 0) if row is not None else 0
        processing = int(row["processing"] or 0) if row is not None else 0
        done = int(row["done"] or 0) if row is not None else 0
        cancelled = int(row["cancelled"] or 0) if row is not None else 0
        dead_letter = int(dlq_row["c"] or 0) if dlq_row is not None else 0
        return QueueStats(
            total=queued + processing + done + cancelled + dead_letter,
            queued=queued,
            processing=processing,
            done=done,
            dead_letter=dead_letter,
            cancelled=cancelled,
        )

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
        assert self._conn is not None
        now = _now_iso()
        await self._conn.execute(
            """
            INSERT OR REPLACE INTO soniq_workers
                (id, hostname, pid, queues, concurrency, status, last_heartbeat, started_at, metadata)
            VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?)
            """,
            (
                worker_id,
                hostname,
                pid,
                json.dumps(queues),
                concurrency,
                now,
                now,
                json.dumps(metadata) if metadata else None,
            ),
        )
        await self._conn.commit()

    async def update_heartbeat(
        self, worker_id: str, metadata: Optional[dict] = None
    ) -> None:
        assert self._conn is not None
        await self._conn.execute(
            "UPDATE soniq_workers SET last_heartbeat=?, metadata=? WHERE id=?",
            (_now_iso(), json.dumps(metadata) if metadata else None, worker_id),
        )
        await self._conn.commit()

    async def mark_worker_stopped(self, worker_id: str) -> None:
        assert self._conn is not None
        await self._conn.execute(
            "UPDATE soniq_workers SET status='stopped', last_heartbeat=? WHERE id=?",
            (_now_iso(), worker_id),
        )
        await self._conn.commit()

    async def cleanup_stale_workers(self, stale_threshold_seconds: int) -> int:
        assert self._conn is not None
        threshold = (
            datetime.now(timezone.utc) - timedelta(seconds=stale_threshold_seconds)
        ).isoformat()

        async with self._conn.execute(
            "SELECT id FROM soniq_workers WHERE status='active' AND last_heartbeat < ?",
            (threshold,),
        ) as cursor:
            stale = [row["id"] for row in await cursor.fetchall()]

        if not stale:
            return 0

        placeholders = ",".join("?" for _ in stale)
        await self._conn.execute(
            f"UPDATE soniq_workers SET status='stopped' WHERE id IN ({placeholders})",
            stale,
        )
        await self._conn.execute(
            f"UPDATE soniq_jobs SET status='queued', worker_id=NULL, updated_at=? WHERE status='processing' AND worker_id IN ({placeholders})",
            [_now_iso()] + stale,
        )
        await self._conn.commit()
        return len(stale)

    # Task registry methods are no-ops on SQLite - this metadata targets Postgres deployments.

    async def register_task_name(
        self,
        *,
        task_name: str,
        worker_id: str,
        args_model_repr: Optional[str] = None,
    ) -> None:
        return None

    async def list_registered_task_names(self) -> list[dict]:
        return []

    async def delete_expired_jobs(self) -> int:
        assert self._conn is not None
        cursor = await self._conn.execute(
            "DELETE FROM soniq_jobs WHERE status='done' AND expires_at < ?",
            (_now_iso(),),
        )
        await self._conn.commit()
        return cursor.rowcount or 0  # type: ignore[return-value]

    async def reset(self) -> None:
        assert self._conn is not None
        await self._conn.execute("DELETE FROM soniq_jobs")
        await self._conn.execute("DELETE FROM soniq_workers")
        await self._conn.commit()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sqlite_row_to_dict(row: Any) -> dict:
    """Convert an aiosqlite Row to the standard job dict format. Deserializes TEXT-stored JSON fields."""
    d = dict(row)
    if isinstance(d.get("args"), str):
        try:
            d["args"] = json.loads(d["args"])
        except (json.JSONDecodeError, TypeError):
            pass
    if "result" in d and isinstance(d["result"], str):
        try:
            d["result"] = json.loads(d["result"])
        except (json.JSONDecodeError, TypeError):
            pass
    return d
