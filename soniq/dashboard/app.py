"""
Dashboard data collection and API for Soniq.

The HTTP layer (``server``) constructs a single ``DashboardService``
bound to the configured ``Soniq`` and calls methods on it.
"""

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, AsyncIterator, Dict, List, Optional

if TYPE_CHECKING:
    from soniq.app import Soniq


class DashboardService:
    """Dashboard data layer bound to a Soniq instance.

    Read methods (``get_job_stats``, ``get_recent_jobs``, ...) borrow a
    pooled connection via ``self._app.backend.acquire()``. Write methods
    (``replay_dead_letter``, ``delete_job``, ``cancel_job``) just hit the
    backend; HTTP-level authorization for writes is enforced in
    ``server._require_write_authorization``.
    """

    def __init__(self, app: "Soniq"):
        self._app = app

    @asynccontextmanager
    async def _acquire(self) -> AsyncIterator[Any]:
        await self._app.ensure_initialized()
        async with self._app.backend.acquire() as conn:
            yield conn

    async def get_job_stats(self) -> Dict[str, int]:
        async with self._acquire() as conn:
            stats = await conn.fetchrow(
                """
                SELECT
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE status = 'queued') as queued,
                    COUNT(*) FILTER (WHERE status = 'processing') as processing,
                    COUNT(*) FILTER (WHERE status = 'done') as done,
                    COUNT(*) FILTER (WHERE status = 'cancelled') as cancelled
                FROM soniq_jobs
                """
            )
            dlq_count = await conn.fetchval(
                "SELECT COUNT(*) FROM soniq_dead_letter_jobs"
            )
            return {
                "total": stats["total"],
                "queued": stats["queued"],
                "processing": stats["processing"],
                "done": stats["done"],
                "cancelled": stats["cancelled"],
                "dead_letter": int(dlq_count or 0),
            }

    async def get_recent_jobs(
        self, limit: int = 50, queue: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        async with self._acquire() as conn:
            if queue:
                query = """
                    SELECT id, job_name, status, queue, priority, attempts, max_attempts,
                           created_at, updated_at, scheduled_at, last_error
                    FROM soniq_jobs
                    WHERE queue = $2
                    ORDER BY created_at DESC
                    LIMIT $1
                """
                rows = await conn.fetch(query, limit, queue)
            else:
                query = """
                    SELECT id, job_name, status, queue, priority, attempts, max_attempts,
                           created_at, updated_at, scheduled_at, last_error
                    FROM soniq_jobs
                    ORDER BY created_at DESC
                    LIMIT $1
                """
                rows = await conn.fetch(query, limit)
            return [dict(row) for row in rows]

    async def get_queue_stats(self) -> List[Dict[str, Any]]:
        # Per-queue rollup for the dashboard. Distinct from the canonical
        # whole-instance Soniq.get_queue_stats(); the dashboard breaks down
        # by queue and joins in the DLQ count from soniq_dead_letter_jobs
        # (DLQ Option A). See docs/_internals/contracts/dead_letter.md and
        # docs/_internals/contracts/queue_stats.md.
        async with self._acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    j.queue,
                    COUNT(*) AS total_jobs,
                    COUNT(*) FILTER (WHERE j.status = 'queued')     AS queued,
                    COUNT(*) FILTER (WHERE j.status = 'processing') AS processing,
                    COUNT(*) FILTER (WHERE j.status = 'done')       AS done,
                    COUNT(*) FILTER (WHERE j.status = 'cancelled')  AS cancelled,
                    AVG(GREATEST(EXTRACT(EPOCH FROM (j.updated_at - j.created_at)) * 1000, 0))
                        FILTER (WHERE j.status = 'done')            AS avg_processing_time_ms
                FROM soniq_jobs j
                GROUP BY j.queue
                ORDER BY total_jobs DESC
                """
            )
            dlq_rows = await conn.fetch(
                """
                SELECT queue, COUNT(*) AS dead_letter
                FROM soniq_dead_letter_jobs
                GROUP BY queue
                """
            )
            dlq_by_queue = {r["queue"]: int(r["dead_letter"]) for r in dlq_rows}
            results: List[Dict[str, Any]] = []
            for row in rows:
                d = dict(row)
                d["dead_letter"] = dlq_by_queue.get(d["queue"], 0)
                results.append(d)
            for queue, count in dlq_by_queue.items():
                if not any(r["queue"] == queue for r in results):
                    results.append(
                        {
                            "queue": queue,
                            "total_jobs": count,
                            "queued": 0,
                            "processing": 0,
                            "done": 0,
                            "cancelled": 0,
                            "dead_letter": count,
                            "avg_processing_time_ms": None,
                        }
                    )
            return results

    async def get_job_metrics(self, hours: int = 24) -> Dict[str, Any]:
        async with self._acquire() as conn:
            since = datetime.now() - timedelta(hours=hours)
            metrics = await conn.fetchrow(
                """
                SELECT
                    COUNT(*) as total_processed,
                    COUNT(*) FILTER (WHERE status = 'done') as successful,
                    AVG(GREATEST(EXTRACT(EPOCH FROM (updated_at - created_at)) * 1000, 0))
                        FILTER (WHERE status = 'done') as avg_processing_time_ms,
                    COUNT(*) / GREATEST($2, 1) as jobs_per_hour
                FROM soniq_jobs
                WHERE updated_at >= $1
                """,
                since,
                hours,
            )
            dead_lettered = await conn.fetchval(
                "SELECT COUNT(*) FROM soniq_dead_letter_jobs WHERE moved_to_dead_letter_at >= $1",
                since,
            )
            success_rate = 0.0
            if metrics["total_processed"] > 0:
                success_rate = (
                    metrics["successful"] / metrics["total_processed"]
                ) * 100
            return {
                "total_processed": metrics["total_processed"],
                "successful": metrics["successful"],
                "dead_lettered": int(dead_lettered or 0),
                "success_rate": round(success_rate, 2),
                "avg_processing_time_ms": round(
                    metrics["avg_processing_time_ms"] or 0, 2
                ),
                "jobs_per_hour": round(metrics["jobs_per_hour"] or 0, 2),
                "time_window_hours": hours,
            }

    async def get_job_details(self, job_id: str) -> Optional[Dict[str, Any]]:
        async with self._acquire() as conn:
            try:
                job_uuid = uuid.UUID(job_id)
            except ValueError:
                return None
            job = await conn.fetchrow(
                "SELECT * FROM soniq_jobs WHERE id = $1",
                job_uuid,
            )
            return dict(job) if job else None

    async def replay_dead_letter(self, dead_letter_id: str) -> Optional[str]:
        """Replay a DLQ row through ``DeadLetterService.replay``.

        Returns the new ``soniq_jobs.id`` on success, ``None`` if the row
        is missing or the job is no longer registered.
        """
        try:
            uuid.UUID(dead_letter_id)
        except ValueError:
            return None
        new_job_id: Optional[str] = await self._app.dead_letter.replay(dead_letter_id)
        return new_job_id

    async def delete_job(self, job_id: str) -> bool:
        async with self._acquire() as conn:
            try:
                job_uuid = uuid.UUID(job_id)
            except ValueError:
                return False
            result = await conn.execute(
                "DELETE FROM soniq_jobs WHERE id = $1",
                job_uuid,
            )
            return result == "DELETE 1"  # type: ignore[no-any-return]

    async def cancel_job(self, job_id: str) -> bool:
        async with self._acquire() as conn:
            try:
                job_uuid = uuid.UUID(job_id)
            except ValueError:
                return False
            result = await conn.execute(
                """
                UPDATE soniq_jobs
                SET status = 'cancelled',
                    updated_at = NOW()
                WHERE id = $1
                AND status = 'queued'
                """,
                job_uuid,
            )
            return result == "UPDATE 1"  # type: ignore[no-any-return]

    async def get_worker_stats(self) -> Dict[str, Any]:
        async with self._acquire() as conn:
            active_jobs = await conn.fetchval(
                """
                SELECT COUNT(*) FROM soniq_jobs
                WHERE status = 'queued'
                AND updated_at > NOW() - INTERVAL '5 minutes'
                """
            )
            recent_activity = await conn.fetchrow(
                """
                SELECT
                    COUNT(*) as jobs_last_hour,
                    COUNT(*) FILTER (WHERE status = 'done') as completed_last_hour
                FROM soniq_jobs
                WHERE updated_at > NOW() - INTERVAL '1 hour'
                """
            )
            dead_lettered_last_hour = await conn.fetchval(
                """
                SELECT COUNT(*) FROM soniq_dead_letter_jobs
                WHERE moved_to_dead_letter_at > NOW() - INTERVAL '1 hour'
                """
            )
            return {
                "active_jobs": active_jobs,
                "jobs_last_hour": recent_activity["jobs_last_hour"],
                "completed_last_hour": recent_activity["completed_last_hour"],
                "dead_lettered_last_hour": int(dead_lettered_last_hour or 0),
                "timestamp": datetime.now().isoformat(),
            }

    async def get_job_timeline(self, hours: int = 24) -> List[Dict[str, Any]]:
        async with self._acquire() as conn:
            since = datetime.now() - timedelta(hours=hours)
            timeline = await conn.fetch(
                """
                SELECT
                    DATE_TRUNC('hour', created_at) as hour,
                    COUNT(*) as total_jobs,
                    COUNT(*) FILTER (WHERE status = 'done') as completed
                FROM soniq_jobs
                WHERE created_at >= $1
                GROUP BY DATE_TRUNC('hour', created_at)
                ORDER BY hour ASC
                """,
                since,
            )
            dlq_buckets = await conn.fetch(
                """
                SELECT
                    DATE_TRUNC('hour', moved_to_dead_letter_at) as hour,
                    COUNT(*) as dead_letter
                FROM soniq_dead_letter_jobs
                WHERE moved_to_dead_letter_at >= $1
                GROUP BY DATE_TRUNC('hour', moved_to_dead_letter_at)
                """,
                since,
            )
            dlq_by_hour = {r["hour"]: int(r["dead_letter"]) for r in dlq_buckets}
            return [
                {
                    "hour": row["hour"].isoformat(),
                    "total_jobs": row["total_jobs"],
                    "completed": row["completed"],
                    "dead_letter": dlq_by_hour.get(row["hour"], 0),
                }
                for row in timeline
            ]

    async def get_job_types_stats(self) -> List[Dict[str, Any]]:
        async with self._acquire() as conn:
            stats = await conn.fetch(
                """
                SELECT
                    job_name,
                    COUNT(*) as total_count,
                    COUNT(*) FILTER (WHERE status = 'done') as completed_count,
                    COUNT(*) FILTER (WHERE status = 'queued') as queued_count,
                    AVG(GREATEST(EXTRACT(EPOCH FROM (updated_at - created_at)) * 1000, 0))
                        FILTER (WHERE status = 'done') as avg_processing_time_ms,
                    MAX(updated_at) as last_run
                FROM soniq_jobs
                WHERE created_at > NOW() - INTERVAL '7 days'
                GROUP BY job_name
                ORDER BY total_count DESC
                LIMIT 20
                """
            )
            dlq_rows = await conn.fetch(
                """
                SELECT job_name, COUNT(*) AS dead_letter_count
                FROM soniq_dead_letter_jobs
                WHERE moved_to_dead_letter_at > NOW() - INTERVAL '7 days'
                GROUP BY job_name
                """
            )
            dlq_by_name = {r["job_name"]: int(r["dead_letter_count"]) for r in dlq_rows}
            return [
                {
                    "job_name": row["job_name"],
                    "total_count": row["total_count"],
                    "completed_count": row["completed_count"],
                    "dead_letter_count": dlq_by_name.get(row["job_name"], 0),
                    "queued_count": row["queued_count"],
                    "success_rate": (
                        round((row["completed_count"] / row["total_count"]) * 100, 2)
                        if row["total_count"] > 0
                        else 0
                    ),
                    "avg_processing_time_ms": round(
                        row["avg_processing_time_ms"] or 0, 2
                    ),
                    "last_run": (
                        row["last_run"].isoformat() if row["last_run"] else None
                    ),
                }
                for row in stats
            ]

    async def search_jobs(
        self,
        query: Optional[str] = None,
        status: Optional[str] = None,
        queue: Optional[str] = None,
        job_name: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Dict[str, Any]:
        async with self._acquire() as conn:
            conditions = []
            params = []
            param_count = 0

            if status:
                param_count += 1
                conditions.append(f"status = ${param_count}")
                params.append(status)

            if queue:
                param_count += 1
                conditions.append(f"queue = ${param_count}")
                params.append(queue)

            if job_name:
                param_count += 1
                conditions.append(f"job_name ILIKE ${param_count}")
                params.append(f"%{job_name}%")

            if query:
                param_count += 1
                conditions.append(
                    f"(job_name ILIKE ${param_count} OR last_error ILIKE ${param_count})"
                )
                params.append(f"%{query}%")

            where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""

            count_query = f"SELECT COUNT(*) FROM soniq_jobs {where_clause}"
            total_count = await conn.fetchval(count_query, *params)

            param_count += 1
            limit_param = param_count
            param_count += 1
            offset_param = param_count

            jobs_query = f"""
                SELECT id, job_name, status, queue, priority, attempts, max_attempts,
                       created_at, updated_at, scheduled_at, last_error
                FROM soniq_jobs
                {where_clause}
                ORDER BY created_at DESC
                LIMIT ${limit_param} OFFSET ${offset_param}
            """

            jobs = await conn.fetch(jobs_query, *params, limit, offset)

            return {
                "jobs": [dict(job) for job in jobs],
                "total_count": total_count,
                "limit": limit,
                "offset": offset,
                "has_more": (offset + limit) < total_count,
            }

    async def get_system_health(self) -> Dict[str, Any]:
        async with self._acquire() as conn:
            db_healthy = True
            try:
                await conn.fetchval("SELECT 1")
            except Exception:
                db_healthy = False

            backlog = await conn.fetchval(
                "SELECT COUNT(*) FROM soniq_jobs WHERE status = 'queued'"
            )

            recent_stats = await conn.fetchrow(
                """
                SELECT COUNT(*) as total_recent
                FROM soniq_jobs
                WHERE updated_at > NOW() - INTERVAL '1 hour'
                """
            )
            dead_lettered_recent = await conn.fetchval(
                """
                SELECT COUNT(*) FROM soniq_dead_letter_jobs
                WHERE moved_to_dead_letter_at > NOW() - INTERVAL '1 hour'
                """
            )
            dead_lettered_recent = int(dead_lettered_recent or 0)

            error_rate = 0.0
            if recent_stats["total_recent"] > 0:
                error_rate = (dead_lettered_recent / recent_stats["total_recent"]) * 100

            health_status = "healthy"
            if not db_healthy:
                health_status = "unhealthy"
            elif backlog > 1000:
                health_status = "degraded"
            elif error_rate > 10:
                health_status = "degraded"

            return {
                "status": health_status,
                "database_healthy": db_healthy,
                "queue_backlog": backlog,
                "error_rate_last_hour": round(error_rate, 2),
                "jobs_processed_last_hour": recent_stats["total_recent"],
                "timestamp": datetime.now().isoformat(),
            }

    async def get_task_registry_drift(self, window_minutes: int = 60) -> Dict[str, Any]:
        """Surface deploy-skew: names with queued / dead-lettered rows in the
        last N minutes that have no current row in soniq_task_registry.

        This *reads* the soniq_task_registry table; the enqueue path still
        does not. The query joins soniq_jobs.job_name against
        soniq_task_registry.task_name and surfaces the unmatched names.
        """
        async with self._acquire() as conn:
            rows = await conn.fetch(
                """
                WITH recent AS (
                    SELECT job_name, 'queued'::text AS bucket, updated_at AS seen_at
                    FROM soniq_jobs
                    WHERE updated_at > NOW() - ($1 || ' minutes')::interval
                      AND status = 'queued'
                    UNION ALL
                    SELECT job_name, 'dead_letter'::text AS bucket,
                           moved_to_dead_letter_at AS seen_at
                    FROM soniq_dead_letter_jobs
                    WHERE moved_to_dead_letter_at
                          > NOW() - ($1 || ' minutes')::interval
                )
                SELECT
                    job_name,
                    COUNT(*) FILTER (WHERE bucket = 'queued') AS queued,
                    COUNT(*) FILTER (WHERE bucket = 'dead_letter') AS dead_letter,
                    MAX(seen_at) AS last_seen
                FROM recent
                WHERE NOT EXISTS (
                    SELECT 1 FROM soniq_task_registry AS r
                    WHERE r.task_name = recent.job_name
                )
                GROUP BY job_name
                ORDER BY last_seen DESC
                """,
                str(window_minutes),
            )
            return {
                "window_minutes": window_minutes,
                "skewed_names": [
                    {
                        "job_name": r["job_name"],
                        "queued": r["queued"],
                        "dead_letter": r["dead_letter"],
                        "last_seen": (
                            r["last_seen"].isoformat() if r["last_seen"] else None
                        ),
                    }
                    for r in rows
                ],
            }
