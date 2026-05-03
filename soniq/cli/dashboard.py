"""``soniq dashboard`` - launch the FastAPI dashboard."""

from __future__ import annotations

from ._context import cli_app
from ._helpers import database_url_argument


def add_dashboard_cmd(subparsers) -> None:
    parser = subparsers.add_parser(
        "dashboard",
        help="Launch the Soniq web dashboard",
        description="Start the Soniq web dashboard for monitoring jobs",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    parser.add_argument("--port", type=int, default=6161, help="Port to bind to")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload")
    database_url_argument(parser)
    parser.set_defaults(func=handle_dashboard)


async def handle_dashboard(args) -> int:
    async with cli_app(args) as app:
        from soniq import DASHBOARD_AVAILABLE

        if not DASHBOARD_AVAILABLE:
            print(
                "Dashboard is not available. Install with: pip install soniq[dashboard]"
            )
            return 1

        from soniq.dashboard.server import run_dashboard

        rc = await run_dashboard(soniq_app=app, host=args.host, port=args.port)
        return int(rc) if rc is not None else 0
