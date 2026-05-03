"""``soniq setup`` - apply pending database migrations."""

from __future__ import annotations

from ._context import cli_app
from ._helpers import database_url_argument
from .colors import print_status


def add_setup_cmd(subparsers) -> None:
    parser = subparsers.add_parser(
        "setup",
        help="Setup Soniq database",
        description="Initialize or update Soniq database schema",
    )
    database_url_argument(parser)
    parser.set_defaults(func=handle_setup)


async def handle_setup(args) -> int:
    try:
        async with cli_app(args) as app:
            print_status("Setting up Soniq database...", "info")

            status = await app._get_migration_status(version_filter="000")
            applied_count = await app._run_migrations(version_filter="000")

            print(f"  Found {status['total_migrations']} core migrations")

            if status["pending_migrations"]:
                print(
                    f"  Applying {len(status['pending_migrations'])} pending migrations..."
                )
                for migration in status["pending_migrations"]:
                    print(f"    - {migration}")
            else:
                print("  Core schema is already up to date")

            if applied_count > 0:
                print_status(
                    f"Applied {applied_count} migrations successfully", "success"
                )
            else:
                print_status(
                    "Database setup completed (no migrations needed)", "success"
                )

            return 0
    except Exception as e:
        msg = str(e).lower()
        if "connection" in msg or "connect" in msg:
            print_status("Can't connect to database", "error")
            print("Make sure PostgreSQL is running and your database URL is correct")
        elif "database" in msg and "does not exist" in msg:
            print_status("Database doesn't exist", "error")
            print("Run: createdb your_database_name")
        elif "permission" in msg or "authentication" in msg:
            print_status("Database permission error", "error")
            print("Check your database username/password in the connection URL")
        else:
            print_status(f"Setup failed: {e}", "error")
        return 1
