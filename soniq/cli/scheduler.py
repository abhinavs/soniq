"""``soniq scheduler`` - run the recurring-job scheduler."""

from __future__ import annotations

import asyncio
import os

from soniq.discovery import discover_and_import_modules
from soniq.utils.signals import GracefulSignalHandler

from ._context import execution_app
from ._helpers import (
    app_argument,
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
        help=(
            "How often to check for due jobs in seconds (default: 60). "
            "Lowered automatically to the shortest every= interval."
        ),
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
    app_argument(parser, positional=True)
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

        # SIGTERM is how Docker, Kubernetes and systemd stop a process, so
        # treat it like Ctrl+C instead of dying without stopping the loop.
        stop = asyncio.Event()
        signals = GracefulSignalHandler()
        signals.setup_signal_handlers(stop)
        try:
            await scheduler.start(check_interval=args.check_interval)
            while scheduler.running and not stop.is_set():
                try:
                    await asyncio.wait_for(stop.wait(), timeout=10)
                except asyncio.TimeoutError:
                    pass
            if not stop.is_set():
                print("Scheduler stopped unexpectedly")
                return 0
            print("Stopping scheduler...")
            await scheduler.stop()
            print("Scheduler stopped")
        except Exception as e:
            print(f"Scheduler error: {e}")
            return 1
        finally:
            signals.restore_signal_handlers()
        return 0
