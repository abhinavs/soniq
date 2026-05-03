"""``soniq worker`` - run a worker."""

from __future__ import annotations

import os
import sys

from soniq.discovery import discover_and_import_modules, parse_jobs_modules

from ._context import cli_app
from ._helpers import configure_cli_logging, database_url_argument
from .colors import print_status


def add_worker_cmd(subparsers) -> None:
    parser = subparsers.add_parser(
        "worker",
        help="Run a Soniq worker",
        description="Run a Soniq worker to process background jobs",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=4,
        help="Number of concurrent workers (default: 4)",
    )
    parser.add_argument(
        "--queues",
        default=None,
        help="Comma-separated list of queues to process (default: all queues)",
    )
    parser.add_argument(
        "--jobs-modules",
        default=None,
        help=(
            "Comma-separated list of modules to import on startup. Merged with "
            "SONIQ_JOBS_MODULES (the env var sets the base; this flag adds more) "
            "for per-worker overrides. See docs/getting-started/installation.md."
        ),
    )
    parser.add_argument(
        "--run-once",
        action="store_true",
        help="Process jobs once and exit (useful for testing)",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        help="Root logger level (default: INFO, or $SONIQ_LOG_LEVEL)",
    )
    database_url_argument(parser)
    parser.set_defaults(func=handle_worker)


async def handle_worker(args) -> int:
    log_level: str = (
        getattr(args, "log_level", None) or os.getenv("SONIQ_LOG_LEVEL") or "INFO"
    )
    configure_cli_logging(log_level)

    jobs_modules_env = os.getenv("SONIQ_JOBS_MODULES", "")
    cli_jobs_modules = getattr(args, "jobs_modules", None) or ""

    if not jobs_modules_env and not cli_jobs_modules:
        print(
            "Error: SONIQ_JOBS_MODULES is not set and --jobs-modules was not passed. "
            "Please configure the path to your job modules.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Merge env-var (base) with CLI flag (per-worker addition). Order is
    # preserved and duplicates dropped so we don't import the same module twice.
    env_modules = parse_jobs_modules(jobs_modules_env) if jobs_modules_env else []
    cli_modules = parse_jobs_modules(cli_jobs_modules) if cli_jobs_modules else []
    seen: set[str] = set()
    modules: list[str] = []
    for mod in (*env_modules, *cli_modules):
        if mod not in seen:
            seen.add(mod)
            modules.append(mod)
    if len(modules) == 1:
        print(f"Discovering jobs in: {modules[0]}")
    else:
        print("Discovering jobs in:")
        for mod in modules:
            print(f"  {mod}")

    discover_and_import_modules(modules)
    for mod in modules:
        print(f"  - Imported '{mod}'")

    if args.queues is None:
        queues = None
        queue_msg = "all available queues"
    else:
        queues = [q.strip() for q in args.queues.split(",")]
        queue_msg = ", ".join(queues)

    print_status(f"Starting Soniq worker with {args.concurrency} workers", "info")
    print_status(f"Processing queues: {queue_msg}", "info")

    async with cli_app(args) as app:
        try:
            await app.run_worker(
                concurrency=args.concurrency, queues=queues, run_once=args.run_once
            )
        except KeyboardInterrupt:
            print_status("Worker stopped by user", "info")
            return 0
        except Exception as e:
            print_status(f"Worker error: {e}", "error")
            return 1

    return 0
