"""``soniq dashboard`` - launch the FastAPI dashboard."""

from __future__ import annotations

from soniq.discovery import discover_and_import_modules

from ._context import cli_app, execution_app
from ._helpers import database_url_argument, resolve_jobs_modules
from .colors import print_status


def add_dashboard_cmd(subparsers) -> None:
    parser = subparsers.add_parser(
        "dashboard",
        help="Launch the Soniq web dashboard",
        description="Start the Soniq web dashboard for monitoring jobs",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    parser.add_argument("--port", type=int, default=6161, help="Port to bind to")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload")
    parser.add_argument(
        "--jobs-modules",
        default=None,
        help=(
            "Comma-separated list of modules to import on startup. Merged with "
            "SONIQ_JOBS_MODULES. The dashboard's dead-letter replay needs the "
            "target job's registration loaded, so it must run on the same "
            "instance your job modules registered on (like worker/scheduler)."
        ),
    )
    database_url_argument(parser)
    parser.set_defaults(func=handle_dashboard)


async def handle_dashboard(args) -> int:
    # The dashboard exposes dead-letter replay, which re-inserts a soniq_jobs
    # row and needs the target job's registration to resolve max_attempts. Run
    # on the job-module instance (same reason worker/scheduler do) so that path
    # works; read-only endpoints are unaffected either way.
    #
    # An explicit --database-url is the exception: the dashboard is a monitoring
    # tool and pointing it at an arbitrary database is a legitimate use, so honour
    # the flag over module discovery (replay then only works for that database's
    # own registered jobs, which is inherent).
    modules = resolve_jobs_modules(args)
    if getattr(args, "database_url", None):
        ctx = cli_app(args)
    elif modules:
        discover_and_import_modules(modules)
        ctx = execution_app(args, modules)
    else:
        print_status(
            "No job modules configured (set SONIQ_JOBS_MODULES or pass "
            "--jobs-modules). The dashboard will run, but dead-letter replay "
            "will fail for jobs whose registration isn't loaded.",
            "warning",
        )
        ctx = cli_app(args)

    async with ctx as app:
        from soniq import DASHBOARD_AVAILABLE

        if not DASHBOARD_AVAILABLE:
            print(
                "Dashboard is not available. Install with: pip install soniq[dashboard]"
            )
            return 1

        from soniq.dashboard.server import run_dashboard

        rc = await run_dashboard(soniq_app=app, host=args.host, port=args.port)
        return int(rc) if rc is not None else 0
