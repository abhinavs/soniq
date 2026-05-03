"""``soniq migrate-status`` - show pending vs. applied migrations."""

from __future__ import annotations

from ._context import cli_app
from ._helpers import database_url_argument
from .colors import print_status


def add_migrate_status_cmd(subparsers) -> None:
    parser = subparsers.add_parser(
        "migrate-status",
        help="Show database migration status",
        description="Display current database migration status and pending migrations",
    )
    database_url_argument(parser)
    parser.set_defaults(func=handle_migrate_status)


async def handle_migrate_status(args) -> int:
    async with cli_app(args) as app:
        try:
            print_status("Soniq Database Migration Status", "info")
            print("=" * 50)

            status = await app._get_migration_status()

            print(f"Total migrations: {status['total_migrations']}")
            print(f"Applied migrations: {len(status['applied_migrations'])}")
            print(f"Pending migrations: {len(status['pending_migrations'])}")
            print(
                "Status: "
                + ("Up to date" if status["is_up_to_date"] else "Migrations pending")
            )

            if status["applied_migrations"]:
                print("\nApplied migrations:")
                for migration in status["applied_migrations"]:
                    print(f"  ✅ {migration}")

            if status["pending_migrations"]:
                print("\nPending migrations:")
                for migration in status["pending_migrations"]:
                    print(f"  ⏳ {migration}")
                print("\nRun 'soniq setup' to apply pending migrations")

            return 0
        except Exception as e:
            print_status(f"Failed to get migration status: {e}", "error")
            return 1
