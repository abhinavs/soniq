"""
Shared CLI helpers.

Two utilities every subcommand reaches for:

- ``resolve_soniq_instance(args)``: turn a ``--database-url`` flag into
  a fresh ``Soniq`` instance, or ``None`` when the operator wants the
  global app. Centralised so error messages stay consistent.
- ``resolve_jobs_modules(args)``: merge ``SONIQ_JOBS_MODULES`` with the
  ``--jobs-modules`` flag into one ordered module list, so worker and
  scheduler agree on precedence.
- ``configure_cli_logging(level)``: attach a single stream handler to
  the root logger so long-running commands (worker, scheduler) emit
  job-lifecycle logs to the terminal. Idempotent.

``database_url_argument(parser)`` adds the standard ``--database-url``
flag every command that talks to Postgres needs. Living here keeps the
flag spelt the same way everywhere.

``app_argument(parser)`` adds ``--app module:attribute`` (and, for
``worker``/``setup``/``scheduler``, an optional positional ``APP``) so a
command can load the user's own ``Soniq`` instance. ``load_app(args)``
turns that path into the instance.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import logging
import os
from typing import Any, Optional

from dotenv import dotenv_values
from pydantic import ValidationError

from soniq import Soniq
from soniq.discovery import (
    AmbiguousAppError,
    _ensure_cwd_on_path,
    find_soniq_app,
    merge_module_lists,
)

logger = logging.getLogger(__name__)


def database_url_argument(parser) -> None:
    """Add ``--database-url`` to a subparser."""
    parser.add_argument(
        "--database-url",
        help="Database URL (overrides SONIQ_DATABASE_URL environment variable)",
        metavar="URL",
    )


def resolve_jobs_modules(args: Any) -> list[str]:
    """Merge ``SONIQ_JOBS_MODULES`` (the base) with ``--jobs-modules`` (per-run
    additions) into one ordered, de-duplicated module list.

    ``soniq worker`` and ``soniq scheduler`` both resolve their modules this way,
    so the env-var/flag precedence stays spelt the same in one place.
    """
    raw = os.environ.get("SONIQ_JOBS_MODULES")
    if raw is None:
        raw = dotenv_values(".env").get("SONIQ_JOBS_MODULES", "")
    app_path = getattr(args, "app", None)
    return merge_module_lists(
        # The APP module goes first: it defines the instance, and the env var
        # and flag only add more modules on top.
        app_path.partition(":")[0] if app_path else "",
        raw or "",
        getattr(args, "jobs_modules", None) or "",
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


def _is_app_path(value: str) -> bool:
    """True when ``value`` is ``module:attribute`` or an importable module."""
    if ":" in value:
        return True
    _ensure_cwd_on_path()
    try:
        return importlib.util.find_spec(value) is not None
    except (ImportError, ValueError):
        return False


class _AppAction(argparse.Action):
    """Store an app path in ``args.app``.

    As a positional, a value that is not an app path is handed back to
    argparse as an unrecognized argument, so ``soniq worker typo`` fails the
    same way it did before ``APP`` existed.
    """

    def __call__(self, parser, namespace, value, option_string=None):
        if value is None:
            return
        if option_string is None and not _is_app_path(value):
            namespace.__dict__.setdefault("_unrecognized_args", []).append(value)
            return
        current = getattr(namespace, "app", None)
        if current is not None and current != value:
            parser.error("pass the app once, either as APP or with --app")
        namespace.app = value


def app_argument(parser, positional: bool = False) -> None:
    """Add ``--app`` (and optionally a positional ``APP``) to a subparser.

    Only add the positional to commands with no positionals of their own,
    or argparse would read their arguments as ``APP``.
    """
    help_text = (
        "Your Soniq instance as module:attribute (for example myapp.jobs:soniq). "
        "With a module only, Soniq finds the instance in it."
    )
    parser.add_argument(
        "--app",
        dest="app",
        default=None,
        action=_AppAction,
        metavar="MODULE:ATTR",
        help=help_text,
    )
    if positional:
        parser.add_argument(
            "app_positional",
            nargs="?",
            default=None,
            action=_AppAction,
            metavar="APP",
            help="Same as --app.",
        )


def _app_error(message: str) -> SystemExit:
    return SystemExit(f"Error: {message}")


def _env_database_url() -> Optional[str]:
    url = os.environ.get("SONIQ_DATABASE_URL")
    if url is None:
        url = dotenv_values(".env").get("SONIQ_DATABASE_URL")
    return url or None


def load_app(args: Any) -> Optional[Soniq]:
    """Return the ``Soniq`` instance named by ``--app``/``APP``, or ``None``.

    The database URL follows ``--database-url`` > ``SONIQ_DATABASE_URL`` >
    the URL the instance was built with.
    """
    path = getattr(args, "app", None)
    if not path:
        return None

    module_name, _, attr = path.partition(":")
    _ensure_cwd_on_path()
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as e:
        if e.name and (module_name == e.name or module_name.startswith(e.name + ".")):
            raise _app_error(
                f"can't load app '{path}': module '{module_name}' not found"
            )
        raise _app_error(f"can't load app '{path}': {e}")
    except Exception as e:
        raise _app_error(
            f"can't load app '{path}': importing '{module_name}' failed: {e}"
        )

    if attr:
        if not hasattr(module, attr):
            raise _app_error(
                f"can't load app '{path}': module '{module_name}' has no attribute '{attr}'"
            )
        app = getattr(module, attr)
        if not isinstance(app, Soniq):
            raise _app_error(
                f"can't load app '{path}': '{attr}' is a {type(app).__name__}, not a Soniq instance"
            )
    else:
        try:
            found = find_soniq_app([module_name])
        except AmbiguousAppError as e:
            raise _app_error(f"{e} Pass the instance as {module_name}:<name>.")
        if found is None:
            raise _app_error(
                f"can't load app '{path}': no Soniq instance found in '{module_name}'. "
                f"Pass it as {module_name}:<name>."
            )
        app = found

    url = getattr(args, "database_url", None) or _env_database_url()
    if url and url != app.settings.database_url:
        if app.is_initialized or app.backend is not None:
            raise _app_error(
                f"can't point app '{path}' at {url}: the instance already has a "
                "backend. Remove --database-url / SONIQ_DATABASE_URL, or build "
                "the instance without a backend."
            )
        try:
            app._settings = type(app.settings).model_validate(
                {**app.settings.model_dump(), "database_url": url}
            )
        except ValidationError:
            raise ValueError(f"Invalid database URL: {url}")
    return app
