"""
Shared CLI helpers.

Two utilities every subcommand reaches for:

- ``resolve_soniq_instance(args)``: turn a ``--database-url`` flag into
  a fresh ``Soniq`` instance, or ``None`` when the operator wants the
  global app. Centralised so error messages stay consistent.
- ``configure_cli_logging(level)``: attach a single stream handler to
  the root logger so long-running commands (worker, scheduler) emit
  job-lifecycle logs to the terminal. Idempotent.

``database_url_argument(parser)`` adds the standard ``--database-url``
flag every command that talks to Postgres needs. Living here keeps the
flag spelt the same way everywhere.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from pydantic import ValidationError

from soniq import Soniq

logger = logging.getLogger(__name__)


def database_url_argument(parser) -> None:
    """Add ``--database-url`` to a subparser."""
    parser.add_argument(
        "--database-url",
        help="Database URL (overrides SONIQ_DATABASE_URL environment variable)",
        metavar="URL",
    )


def configure_cli_logging(level: str = "INFO") -> None:
    """Attach a single root stream handler so CLI runs surface logs.

    Safe to call multiple times - it tags its handler with
    ``_soniq_cli_handler`` and skips re-adding when one is already
    attached.
    """
    root = logging.getLogger()
    try:
        resolved = getattr(logging, str(level).upper())
    except AttributeError:
        resolved = logging.INFO
    root.setLevel(resolved)
    already_configured = any(
        getattr(h, "_soniq_cli_handler", False) for h in root.handlers
    )
    if not already_configured:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        handler._soniq_cli_handler = True  # type: ignore[attr-defined]
        root.addHandler(handler)


async def resolve_soniq_instance(args: Any) -> Optional[Soniq]:
    """Build a ``Soniq`` from ``--database-url``, or return ``None``.

    Returning ``None`` is the signal to the caller to construct a Soniq
    from default settings. Exists to keep the error-message wording
    consistent across every subcommand that takes a database URL.
    """
    if hasattr(args, "database_url") and args.database_url:
        try:
            return Soniq(database_url=args.database_url)
        except ValidationError as e:
            if "database_url" in str(e):
                if "postgresql" in str(e).lower():
                    raise ValueError(
                        f"Invalid database URL: {args.database_url}\n"
                        "Soniq requires PostgreSQL URLs like: "
                        "postgresql://user:password@localhost/database"
                    )
                raise ValueError(f"Invalid database URL: {args.database_url}")
            raise ValueError(f"Configuration error: {e}")
        except Exception as e:
            msg = str(e).lower()
            if "connect" in msg or "connection" in msg:
                raise ValueError(
                    f"Can't connect to database: {args.database_url}\n"
                    "Make sure PostgreSQL is running and the URL is correct"
                )
            raise ValueError(f"Database configuration error: {e}")

    return None
