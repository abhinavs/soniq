"""``soniq status`` - print queue and worker health."""

from __future__ import annotations

import logging

from ._context import cli_app
from ._helpers import database_url_argument
from .colors import StatusIcon, print_status

logger = logging.getLogger(__name__)


def add_status_cmd(subparsers) -> None:
    parser = subparsers.add_parser(
        "status",
        help="Show system status",
        description="Display system health, job statistics, and queue information",
    )
    parser.add_argument("--jobs", action="store_true", help="Show recent jobs")
    parser.add_argument(
        "--verbose", action="store_true", help="Show detailed information"
    )
    database_url_argument(parser)
    parser.set_defaults(func=handle_status)


async def handle_status(args) -> int:
    async with cli_app(args) as app:
        await app._ensure_initialized()
        print(f"\n{StatusIcon.rocket()} Soniq System Status")

        try:
            async with app.backend.acquire() as conn:
                result = await conn.fetchval("SELECT 1")
            if result == 1:
                print_status("Database connection: OK", "success")
            else:
                print_status("Database connection: FAILED", "error")
                return 1
        except Exception as e:
            print_status(f"Health check failed: {e}", "error")
            return 1

        try:
            stats = await app.get_queue_stats()
            total_jobs = stats["total"]
            total_queued = stats["queued"]
            total_dead_letter = stats["dead_letter"]

            if total_jobs > 0:
                print("\nQueue Statistics:")
                print(f"  Total Jobs: {total_jobs}")
                print(f"  Queued: {total_queued}")
                print(f"  Processing: {stats['processing']}")
                print(f"  Done: {stats['done']}")
                print(f"  Cancelled: {stats['cancelled']}")
                print(f"  Dead Letter: {total_dead_letter}")

                if total_queued > 0:
                    print_status(
                        f"{total_queued} jobs waiting to be processed", "warning"
                    )
                elif total_dead_letter > 0:
                    print_status(
                        f"{total_dead_letter} jobs in dead-letter queue", "warning"
                    )
                else:
                    print_status("All jobs processed successfully", "success")
            else:
                print_status("No jobs found", "info")
        except Exception as e:
            print_status(f"Failed to get queue stats: {e}", "error")
            return 1

        try:
            worker_status = await app.backend.get_worker_status()
            status_counts = worker_status.get("status_counts", {})
            active_count = status_counts.get("active", 0)
            stale_count = len(worker_status.get("stale_workers", []))

            if active_count > 0 or stale_count > 0:
                worker_summary = f"{active_count} active"
                if stale_count > 0:
                    worker_summary += f", {stale_count} stale"
                worker_summary += " (use 'soniq inspect' for details)"
                print(f"\nWorkers: {worker_summary}")
            else:
                print("\nWorkers: None running (use 'soniq worker' to begin)")
        except Exception as e:
            logger.debug(f"Could not get worker summary: {e}")

        if args.jobs:
            try:
                recent_jobs = await app.list_jobs(limit=10)
                if recent_jobs:
                    print(f"\nRecent Jobs ({len(recent_jobs)}):")
                    print(f"{'ID'[:8]:<8} {'Name':<25} {'Status':<12} {'Queue':<10}")
                    print("-" * 60)
                    for job in recent_jobs:
                        job_name = job["job_name"].split(".")[-1]
                        print(
                            f"{job['id'][:8]:<8} {job_name:<25} "
                            f"{job['status']:<12} {job['queue']:<10}"
                        )
                else:
                    print("  No recent jobs")
            except Exception as e:
                print_status(f"Failed to get recent jobs: {e}", "error")

        return 0
