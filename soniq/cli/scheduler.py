"""``soniq scheduler`` - run the recurring-job scheduler."""

from __future__ import annotations

import asyncio
import os

from soniq.discovery import discover_and_import_modules

from ._context import execution_app
from ._helpers import (
    configure_cli_logging,
    database_url_argument,
    resolve_jobs_modules,
)
from .colors import print_status


def add_scheduler_cmd(subparsers) -> None:
    parser = subparsers.add_parser(
        "scheduler",
        help="Run the Soniq recurring job scheduler",
        description="Start the recurring job scheduler daemon",
    )
    parser.add_argument(
        "--check-interval",
        type=int,
        default=60,
        help="How often to check for due jobs in seconds (default: 60)",
    )
    parser.add_argument(
        "--jobs-modules",
        default=None,
        help=(
            "Comma-separated list of modules to import on startup. Merged with "
            "SONIQ_JOBS_MODULES. The scheduler must import your @app.periodic "
            "definitions to know what to fire."
        ),
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Root logger level (default: INFO, or $SONIQ_LOG_LEVEL)",
    )
    database_url_argument(parser)
    parser.set_defaults(func=handle_scheduler)


async def handle_scheduler(args) -> int:
    log_level: str = (
        getattr(args, "log_level", None) or os.getenv("SONIQ_LOG_LEVEL") or "INFO"
    )
    configure_cli_logging(log_level)

    # Import the modules that declare @app.periodic jobs so the scheduler runs
    # on the same instance they registered on (same reason the worker does).
    modules = resolve_jobs_modules(args)
    if modules:
        discover_and_import_modules(modules)
    else:
        print_status(
            "No job modules configured (set SONIQ_JOBS_MODULES or pass "
            "--jobs-modules). The scheduler can only fire @app.periodic jobs "
            "from modules it imports, so it will run but schedule nothing.",
            "warning",
        )

    async with execution_app(args, modules) as app:
        scheduler = app.scheduler

        print(
            f"Starting Soniq recurring scheduler (checking every {args.check_interval}s)"
        )
        print("Use Ctrl+C to stop gracefully")

        try:
            await scheduler.start(check_interval=args.check_interval)
            while scheduler.running:
                await asyncio.sleep(10)
            print("Scheduler stopped unexpectedly")
        except KeyboardInterrupt:
            print("Stopping scheduler...")
            await scheduler.stop()
            print("Scheduler stopped")
        except Exception as e:
            print(f"Scheduler error: {e}")
            return 1
        return 0
