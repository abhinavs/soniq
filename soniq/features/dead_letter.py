"""
Dead Letter Queue Management.
Management of permanently failed jobs, replay, bulk operations.
"""

import csv
import json
import logging
import re
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any, AsyncIterator, Dict, List, Optional, Tuple

from soniq.backends.helpers import rows_affected as _rows_affected

if TYPE_CHECKING:
    from soniq.app import Soniq

logger = logging.getLogger(__name__)

_VALID_TABLE_NAME = re.compile(r"^[a-z_][a-z0-9_]*$")


class DeadLetterReason(str, Enum):
    """Reasons for jobs ending up in dead letter queue"""

    MAX_RETRIES_EXCEEDED = "max_retries_exceeded"
    PERMANENT_FAILURE = "permanent_failure"
    JOB_NOT_FOUND = "job_not_found"
    INVALID_ARGUMENTS = "invalid_arguments"
    TIMEOUT = "timeout"
    RESOURCE_EXHAUSTED = "resource_exhausted"
    MANUAL_MOVE = "manual_move"


@dataclass
class DeadLetterJob:
    """Dead letter job record"""

    id: str
    job_name: str
    args: Dict[str, Any]
    queue: str
    priority: int
    max_attempts: int
    attempts: int
    last_error: str
    dead_letter_reason: str
    original_created_at: datetime
    moved_to_dead_letter_at: datetime
    resurrection_count: int = 0
    last_resurrection_at: Optional[datetime] = None
    tags: Optional[Dict[str, str]] = None

    @classmethod
    def from_job_record(
        cls, job_record: Dict[str, Any], reason: DeadLetterReason
    ) -> "DeadLetterJob":
        """Create dead letter job from regular job record"""
        return cls(
            id=str(job_record["id"]),
            job_name=job_record["job_name"],
            args=job_record["args"],
            queue=job_record["queue"],
            priority=job_record["priority"],
            max_attempts=job_record["max_attempts"],
            attempts=job_record["attempts"],
            last_error=job_record["last_error"] or "",
            dead_letter_reason=reason.value,
            original_created_at=job_record["created_at"],
            moved_to_dead_letter_at=datetime.now(timezone.utc),
        )


@dataclass
class DeadLetterStats:
    """Dead letter queue statistics"""

    total_count: int
    by_job_name: Dict[str, int]
    by_queue: Dict[str, int]
    by_reason: Dict[str, int]
    by_date: Dict[str, int]
    oldest_job_age_hours: float
    resurrection_success_rate: float


class DeadLetterFilter:
    """Filter for dead letter queue queries"""

    def __init__(self):
        self.job_names: Optional[List[str]] = None
        self.queues: Optional[List[str]] = None
        self.reasons: Optional[List[str]] = None
        self.date_from: Optional[datetime] = None
        self.date_to: Optional[datetime] = None
        self.tags: Optional[Dict[str, str]] = None
        self.has_been_resurrected: Optional[bool] = None
        self.limit: int = 1000
        self.offset: int = 0

    def to_sql_conditions(self) -> Tuple[List[str], List[Any]]:
        """Convert filter to SQL WHERE conditions and parameters"""
        conditions: List[str] = []
        params: List[Any] = []
        param_count = 0

        if self.job_names:
            param_count += 1
            conditions.append(f"job_name = ANY(${param_count})")
            params.append(self.job_names)

        if self.queues:
            param_count += 1
            conditions.append(f"queue = ANY(${param_count})")
            params.append(self.queues)

        if self.reasons:
            param_count += 1
            conditions.append(f"dead_letter_reason = ANY(${param_count})")
            params.append(self.reasons)

        if self.date_from:
            param_count += 1
            conditions.append(f"moved_to_dead_letter_at >= ${param_count}")
            params.append(self.date_from)

        if self.date_to:
            param_count += 1
            conditions.append(f"moved_to_dead_letter_at <= ${param_count}")
            params.append(self.date_to)

        if self.has_been_resurrected is not None:
            if self.has_been_resurrected:
                conditions.append("resurrection_count > 0")
            else:
                conditions.append("resurrection_count = 0")

        if self.tags:
            for key, value in self.tags.items():
                param_count += 2
                conditions.append(f"tags ->> ${param_count - 1} = ${param_count}")
                params.extend([key, value])

        return conditions, params


class DeadLetterService:
    """Service for dead letter queue operations bound to a Soniq instance.

    Connections come from ``self._app.backend.acquire()`` so a custom
    ``Soniq(...)`` instance writes to its own database.
    """

    def __init__(
        self,
        app: "Soniq",
        *,
        table_name: str = "soniq_dead_letter_jobs",
    ):
        if not _VALID_TABLE_NAME.match(table_name):
            raise ValueError(f"Invalid table name: {table_name!r}")
        self._app = app
        self.table_name = table_name

    @asynccontextmanager
    async def _acquire(self) -> AsyncIterator[Any]:
        await self._app.ensure_initialized()
        async with self._app.backend.acquire() as conn:
            yield conn

    # The DLQ move is a backend primitive (``backend.mark_job_dead_letter``)
    # and the processor is its only legitimate caller. Application code that
    # needs to forcibly DLQ a job should re-enqueue with ``max_attempts=0`` or
    # raise from a handler. See docs/_internals/contracts/dead_letter.md.

    async def replay(
        self,
        dead_letter_id: str,
        reset_attempts: bool = True,
        new_max_attempts: Optional[int] = None,
        new_priority: Optional[int] = None,
        new_queue: Optional[str] = None,
    ) -> Optional[str]:
        """Replay a job from the dead letter queue.

        Inserts a new ``soniq_jobs`` row (fresh id, ``status='queued'``,
        ``attempts=0`` by default) and increments ``resurrection_count``
        on the DLQ row in the same transaction. The DLQ row is preserved
        as the audit trail; operators can replay the same row multiple
        times. See ``docs/_internals/contracts/dead_letter.md``.
        """
        async with self._acquire() as conn:
            async with conn.transaction():
                dead_job = await conn.fetchrow(
                    f"""
                    SELECT * FROM {self.table_name} WHERE id = $1
                    FOR UPDATE
                """,
                    uuid.UUID(dead_letter_id),
                )

                if not dead_job:
                    logger.warning(f"Dead letter job {dead_letter_id} not found")
                    return None

                job_meta = self._app.registry.get_job(dead_job["job_name"])
                if not job_meta:
                    logger.error(
                        f"Cannot replay job {dead_letter_id}: job {dead_job['job_name']} not registered"
                    )
                    return None

                new_job_id = str(uuid.uuid4())

                attempts = 0 if reset_attempts else dead_job["attempts"]
                max_attempts = new_max_attempts or dead_job["max_attempts"]
                priority = new_priority or dead_job["priority"]
                queue = new_queue or dead_job["queue"]

                await conn.execute(
                    """
                    INSERT INTO soniq_jobs (
                        id, job_name, args, max_attempts, priority, queue,
                        attempts, status, scheduled_at
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, 'queued', NOW())
                """,
                    uuid.UUID(new_job_id),
                    dead_job["job_name"],
                    dead_job["args"],
                    max_attempts,
                    priority,
                    queue,
                    attempts,
                )

                await conn.execute(
                    f"""
                    UPDATE {self.table_name}
                    SET resurrection_count = resurrection_count + 1,
                        last_resurrection_at = NOW()
                    WHERE id = $1
                """,
                    uuid.UUID(dead_letter_id),
                )

                logger.info(f"Replayed job {dead_letter_id} as {new_job_id}")
                return new_job_id

    async def bulk_replay(
        self,
        filter_criteria: DeadLetterFilter,
        reset_attempts: bool = True,
        new_max_attempts: Optional[int] = None,
    ) -> List[str]:
        """Replay multiple jobs matching filter criteria."""
        dead_jobs = await self.list_dead_letter_jobs(filter_criteria)

        replayed_jobs = []
        for dead_job in dead_jobs:
            try:
                new_job_id = await self.replay(
                    dead_job.id,
                    reset_attempts=reset_attempts,
                    new_max_attempts=new_max_attempts,
                )
                if new_job_id:
                    replayed_jobs.append(new_job_id)
            except Exception as e:
                logger.error(f"Failed to replay job {dead_job.id}: {e}")

        logger.info(f"Bulk replayed {len(replayed_jobs)} jobs")
        return replayed_jobs

    async def delete_dead_letter_job(self, dead_letter_id: str) -> bool:
        """Permanently delete a dead letter job"""
        async with self._acquire() as conn:
            result = await conn.execute(
                f"""
                DELETE FROM {self.table_name} WHERE id = $1
            """,
                uuid.UUID(dead_letter_id),
            )

            deleted = _rows_affected(result) == 1
            if deleted:
                logger.info(f"Permanently deleted dead letter job {dead_letter_id}")
            return deleted

    async def bulk_delete(self, filter_criteria: DeadLetterFilter) -> int:
        """Delete multiple dead letter jobs matching filter criteria"""
        conditions, params = filter_criteria.to_sql_conditions()

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        async with self._acquire() as conn:
            result = await conn.execute(
                f"""
                DELETE FROM {self.table_name} {where_clause}
            """,
                *params,
            )

            deleted_count = _rows_affected(result)
            logger.info(f"Bulk deleted {deleted_count} dead letter jobs")
            return deleted_count

    async def list_dead_letter_jobs(
        self, filter_criteria: Optional[DeadLetterFilter] = None
    ) -> List[DeadLetterJob]:
        """List dead letter jobs with optional filtering"""
        if filter_criteria is None:
            filter_criteria = DeadLetterFilter()

        conditions, params = filter_criteria.to_sql_conditions()

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        # Add limit and offset as parameterized values
        param_count = len(params)
        param_count += 1
        limit_param = f"${param_count}"
        params.append(filter_criteria.limit)
        param_count += 1
        offset_param = f"${param_count}"
        params.append(filter_criteria.offset)

        async with self._acquire() as conn:
            records = await conn.fetch(
                f"""
                SELECT * FROM {self.table_name}
                {where_clause}
                ORDER BY moved_to_dead_letter_at DESC
                LIMIT {limit_param} OFFSET {offset_param}
            """,
                *params,
            )

            dead_jobs = []
            for record in records:
                dead_job = DeadLetterJob(
                    id=str(record["id"]),
                    job_name=record["job_name"],
                    args=record["args"],
                    queue=record["queue"],
                    priority=record["priority"],
                    max_attempts=record["max_attempts"],
                    attempts=record["attempts"],
                    last_error=record["last_error"] or "",
                    dead_letter_reason=record["dead_letter_reason"],
                    original_created_at=record["original_created_at"],
                    moved_to_dead_letter_at=record["moved_to_dead_letter_at"],
                    resurrection_count=record["resurrection_count"],
                    last_resurrection_at=record["last_resurrection_at"],
                    tags=record["tags"],
                )
                dead_jobs.append(dead_job)

            return dead_jobs

    async def get_dead_letter_job(self, dead_letter_id: str) -> Optional[DeadLetterJob]:
        """Get a specific dead letter job by ID"""
        filter_criteria = DeadLetterFilter()
        filter_criteria.limit = 1

        async with self._acquire() as conn:
            record = await conn.fetchrow(
                f"""
                SELECT * FROM {self.table_name} WHERE id = $1
            """,
                uuid.UUID(dead_letter_id),
            )

            if not record:
                return None

            return DeadLetterJob(
                id=str(record["id"]),
                job_name=record["job_name"],
                args=record["args"],
                queue=record["queue"],
                priority=record["priority"],
                max_attempts=record["max_attempts"],
                attempts=record["attempts"],
                last_error=record["last_error"] or "",
                dead_letter_reason=record["dead_letter_reason"],
                original_created_at=record["original_created_at"],
                moved_to_dead_letter_at=record["moved_to_dead_letter_at"],
                resurrection_count=record["resurrection_count"],
                last_resurrection_at=record["last_resurrection_at"],
                tags=record["tags"],
            )

    async def get_dead_letter_stats(
        self, hours: Optional[int] = None
    ) -> DeadLetterStats:
        """Get dead letter queue statistics"""
        async with self._acquire() as conn:
            # Base query conditions
            time_condition = ""
            params = []
            if hours:
                time_condition = "WHERE moved_to_dead_letter_at >= NOW() - ($1 || ' hours')::INTERVAL"
                params.append(str(hours))

            # Total count
            total_count = await conn.fetchval(
                f"""
                SELECT COUNT(*) FROM {self.table_name} {time_condition}
            """,
                *params,
            )

            # By job name
            by_job_name = await conn.fetch(
                f"""
                SELECT job_name, COUNT(*) as count
                FROM {self.table_name} {time_condition}
                GROUP BY job_name
                ORDER BY count DESC
            """,
                *params,
            )

            # By queue
            by_queue = await conn.fetch(
                f"""
                SELECT queue, COUNT(*) as count
                FROM {self.table_name} {time_condition}
                GROUP BY queue
                ORDER BY count DESC
            """,
                *params,
            )

            # By reason
            by_reason = await conn.fetch(
                f"""
                SELECT dead_letter_reason, COUNT(*) as count
                FROM {self.table_name} {time_condition}
                GROUP BY dead_letter_reason
                ORDER BY count DESC
            """,
                *params,
            )

            # By date (last 7 days)
            by_date = await conn.fetch(
                f"""
                SELECT 
                    DATE(moved_to_dead_letter_at) as date,
                    COUNT(*) as count
                FROM {self.table_name}
                WHERE moved_to_dead_letter_at >= NOW() - INTERVAL '7 days'
                GROUP BY date
                ORDER BY date DESC
            """
            )

            # Oldest job age
            oldest_job = await conn.fetchval(
                f"""
                SELECT MIN(moved_to_dead_letter_at) FROM {self.table_name} {time_condition}
            """,
                *params,
            )

            oldest_age_hours = 0.0
            if oldest_job:
                now = datetime.now(timezone.utc)
                if oldest_job.tzinfo is None:
                    oldest_job = oldest_job.replace(tzinfo=timezone.utc)
                else:
                    oldest_job = oldest_job.astimezone(timezone.utc)
                oldest_age_hours = (now - oldest_job).total_seconds() / 3600

            # Resurrection success rate
            resurrection_stats = await conn.fetchrow(
                f"""
                SELECT 
                    COUNT(*) as total_resurrections,
                    SUM(CASE WHEN resurrection_count > 0 THEN 1 ELSE 0 END) as successful_resurrections
                FROM {self.table_name} {time_condition}
            """,
                *params,
            )

            resurrection_success_rate = 0.0
            if resurrection_stats["total_resurrections"] > 0:
                resurrection_success_rate = (
                    resurrection_stats["successful_resurrections"]
                    / resurrection_stats["total_resurrections"]
                    * 100
                )

            return DeadLetterStats(
                total_count=total_count,
                by_job_name={row["job_name"]: row["count"] for row in by_job_name},
                by_queue={row["queue"]: row["count"] for row in by_queue},
                by_reason={
                    row["dead_letter_reason"]: row["count"] for row in by_reason
                },
                by_date={str(row["date"]): row["count"] for row in by_date},
                oldest_job_age_hours=oldest_age_hours,
                resurrection_success_rate=resurrection_success_rate,
            )

    async def cleanup_old_dead_letter_jobs(self, days: int = 30) -> int:
        """Clean up dead letter jobs older than specified days"""
        async with self._acquire() as conn:
            result = await conn.execute(
                f"""
                DELETE FROM {self.table_name}
                WHERE moved_to_dead_letter_at < NOW() - ($1 || ' days')::INTERVAL
            """,
                str(days),
            )

            deleted_count = _rows_affected(result)
            logger.info(
                f"Cleaned up {deleted_count} dead letter jobs older than {days} days"
            )
            return deleted_count

    async def add_tags_to_job(self, dead_letter_id: str, tags: Dict[str, str]) -> bool:
        """Add tags to a dead letter job"""
        async with self._acquire() as conn:
            # Get current tags
            current_tags = await conn.fetchval(
                f"""
                SELECT tags FROM {self.table_name} WHERE id = $1
            """,
                uuid.UUID(dead_letter_id),
            )

            # JSONB codec returns a dict/None directly.
            if current_tags:
                current_tags.update(tags)
            else:
                current_tags = tags

            # Update tags
            result = await conn.execute(
                f"""
                UPDATE {self.table_name}
                SET tags = $1
                WHERE id = $2
            """,
                current_tags,
                uuid.UUID(dead_letter_id),
            )

            return _rows_affected(result) == 1

    async def export_dead_letter_jobs(
        self, filter_criteria: Optional[DeadLetterFilter] = None, format: str = "json"
    ) -> str:
        """Export dead letter jobs to file"""
        jobs = await self.list_dead_letter_jobs(filter_criteria)

        if format.lower() == "json":
            data = [asdict(job) for job in jobs]
            filename = (
                f"dead_letter_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            )

            with open(filename, "w") as f:
                json.dump(data, f, indent=2, default=str)

            logger.info(f"Exported {len(jobs)} dead letter jobs to {filename}")
            return filename

        elif format.lower() == "csv":
            filename = (
                f"dead_letter_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            )

            if jobs:
                with open(filename, "w", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=asdict(jobs[0]).keys())
                    writer.writeheader()
                    for job in jobs:
                        row = asdict(job)
                        # Convert complex fields to strings
                        row["args"] = json.dumps(row["args"])
                        if row["tags"]:
                            row["tags"] = json.dumps(row["tags"])
                        writer.writerow(row)

            logger.info(f"Exported {len(jobs)} dead letter jobs to {filename}")
            return filename

        else:
            raise ValueError(f"Unsupported export format: {format}")
