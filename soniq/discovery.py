"""
Module discovery for Soniq.

Handles importing job modules specified via SONIQ_JOBS_MODULES.
Supports comma-separated lists and auto-fixes sys.path for project root resolution.
"""

import importlib
import logging
import os
import sys
from typing import List

logger = logging.getLogger(__name__)


def _ensure_cwd_on_path() -> None:
    """
    Add the current working directory to sys.path if not already present.

    This ensures that `importlib.import_module('app.tasks')` works when
    running from the project root without requiring PYTHONPATH to be set.
    Every comparable tool (Celery, RQ, Dramatiq) does this silently.
    """
    cwd = os.getcwd()
    if cwd not in sys.path:
        sys.path.insert(0, cwd)


def parse_jobs_modules(raw: str) -> List[str]:
    """
    Parse SONIQ_JOBS_MODULES into a list of module paths.

    Accepts a comma-separated string. Strips whitespace around commas.
    Skips empty strings from stray commas.

    Examples:
        "app.tasks"                          → ["app.tasks"]
        "app.tasks, billing.tasks"           → ["app.tasks", "billing.tasks"]
        "app.tasks,,  ,billing.tasks"        → ["app.tasks", "billing.tasks"]
    """
    return [m.strip() for m in raw.split(",") if m.strip()]


def _print_import_error(module_path: str, exc: Exception) -> None:
    """Format an import error with contextual hints."""
    print(
        f"Error: Could not import module '{module_path}'.",
        file=sys.stderr,
    )
    print(f"  Details: {exc}", file=sys.stderr)

    # Show PYTHONPATH hint only for "No module named" errors.
    # Syntax errors, missing dependencies, etc. have different causes.
    if "No module named" in str(exc):
        print(
            "  Hint: Make sure you are running soniq from your project root, or",
            file=sys.stderr,
        )
        print(
            "  add your project root to PYTHONPATH.",
            file=sys.stderr,
        )
        print(
            "  Example: PYTHONPATH=/path/to/project soniq worker ...",
            file=sys.stderr,
        )


def discover_and_import_modules(module_paths: List[str]) -> None:
    """
    Import all listed job modules. Adds cwd to sys.path first.

    Collects all failures and reports them together before exiting.
    If any module fails to import, prints all errors and raises SystemExit(1).

    Args:
        module_paths: List of dotted module paths to import.
    """
    _ensure_cwd_on_path()

    failed: list[tuple[str, Exception]] = []
    for path in module_paths:
        try:
            importlib.import_module(path)
            logger.info("Imported jobs module: %s", path)
        except Exception as e:
            failed.append((path, e))

    if failed:
        for path, exc in failed:
            _print_import_error(path, exc)
        raise SystemExit(1)
