"""``soniq inspect`` - inspect worker registrations and heartbeats."""

from __future__ import annotations

from ._context import cli_app
from ._helpers import database_url_argument
from .colors import StatusIcon, print_status


def add_inspect_cmd(subparsers) -> None:
    parser = subparsers.add_parser(
        "inspect",
        help="Inspect workers and recurring schedules",
        description=(
            "Display active workers, heartbeats, recurring-schedule summary, "
            "and monitoring information."
        ),
    )
    parser.add_argument("--stale", action="store_true", help="Show stale/dead workers")
    parser.add_argument(
        "--cleanup", action="store_true", help="Clean up stale worker records"
    )
    parser.add_argument(
        "--schedules",
        action="store_true",
        help="List each recurring schedule (name, status, next run)",
    )
    database_url_argument(parser)
    parser.set_defaults(func=handle_inspect)


async def handle_inspect(args) -> int:
    async with cli_app(args) as app:
        try:
            await app._ensure_initialized()
            print(f"\n{StatusIcon.workers()} Soniq Worker Status")

            if args.cleanup:
                print_status("Cleaning up stale worker records...", "info")
                stale_threshold = int(app.settings.heartbeat_timeout)
                cleaned = await app.backend.cleanup_stale_workers(stale_threshold)
                if cleaned > 0:
                    print_status(
                        f"Cleaned up {cleaned} stale worker records", "success"
                    )
                else:
                    print_status("No stale workers found", "info")
                print()

            worker_status = await app.backend.get_worker_status()

            status_counts = worker_status.get("status_counts", {})
            active_count = status_counts.get("active", 0)
            stopped_count = status_counts.get("stopped", 0)
            total_concurrency = worker_status.get("total_concurrency", 0)
            health = worker_status.get("health", "unknown")

            print(f"Health: {health.upper()}")
            print(f"Active Workers: {active_count}")
            print(f"Stopped Workers: {stopped_count}")
            print(f"Total Concurrency: {total_concurrency}")

            active_workers = worker_status.get("active_workers", [])
            if active_workers:
                print("\nActive Workers:")
                for worker in active_workers:
                    uptime = int(worker["uptime_seconds"])
                    uptime_str = (
                        f"{uptime // 3600}h {(uptime % 3600) // 60}m {uptime % 60}s"
                    )
                    queues_str = (
                        ", ".join(worker["queues"])
                        if worker["queues"]
                        else "all queues"
                    )

                    print(f"  🟢 {worker['hostname']}:{worker['pid']}")
                    print(f"     Queues: {queues_str}")
                    print(f"     Concurrency: {worker['concurrency']}")
                    print(f"     Uptime: {uptime_str}")
                    print(f"     Last Heartbeat: {worker['last_heartbeat']}")

                    metadata = worker.get("metadata", {})
                    if isinstance(metadata, dict) and metadata:
                        if "cpu_percent" in metadata:
                            print(f"     CPU: {metadata['cpu_percent']}%")
                        if "memory_mb" in metadata:
                            print(f"     Memory: {metadata['memory_mb']} MB")
                    print()

            stale_workers = worker_status.get("stale_workers", [])
            if stale_workers and (args.stale or health == "degraded"):
                print("\nStale Workers (no recent heartbeat):")
                for worker in stale_workers:
                    print(f"  🔴 {worker['hostname']}:{worker['pid']}")
                    print(f"     Last Heartbeat: {worker['last_heartbeat']}")
                    print()

                if not args.cleanup:
                    print("Use --cleanup to remove stale worker records")

            if not active_workers and not stale_workers:
                print("\nNo workers found. Start workers with: soniq worker")

            await _print_scheduler_section(app, show_each=args.schedules)

            return 0
        except Exception as e:
            print_status(f"Failed to get worker status: {e}", "error")
            return 1


async def _print_scheduler_section(app, show_each: bool) -> None:
    """Print recurring-schedule summary.

    Scheduler liveness is leader-elected per tick (no persistent process
    record), so we report registered schedules rather than "is a scheduler
    running" - the latter is unanswerable from the database alone.
    """
    try:
        schedules = await app.scheduler.list()
    except Exception as e:
        print(f"\nScheduler: failed to query ({e})")
        return

    active = [s for s in schedules if s["status"] == "active"]
    paused = [s for s in schedules if s["status"] == "paused"]

    print("\nRecurring Schedules:")
    if not schedules:
        print("  (none registered)")
        print(
            "  Register with @app.periodic(...) or app.scheduler.add(...); "
            "run 'soniq scheduler' to dispatch."
        )
        return

    print(f"  Total: {len(schedules)} ({len(active)} active, {len(paused)} paused)")

    if show_each:
        for s in schedules:
            marker = "🟢" if s["status"] == "active" else "⏸️ "
            next_run = s.get("next_run") or "-"
            schedule_value = s.get("schedule_value") or ""
            print(f"  {marker} {s['name']}  [{schedule_value}]")
            print(f"     Status: {s['status']}  Next run: {next_run}")
