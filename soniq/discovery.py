"""
Module discovery for Soniq.

Handles importing job modules specified via SONIQ_JOBS_MODULES.
Supports comma-separated lists and auto-fixes sys.path for project root resolution.
"""

from __future__ import annotations

import importlib
import logging
import os
import sys
from typing import TYPE_CHECKING, List, NoReturn, Optional

if TYPE_CHECKING:
    from soniq import Soniq

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


def merge_module_lists(*sources: str) -> List[str]:
    """Merge comma-separated module strings into one ordered, de-duplicated list.

    Each source is parsed with :func:`parse_jobs_modules`, then the sources are
    concatenated in the order given (env base first, CLI additions after) with
    duplicates dropped, keeping the first occurrence. ``soniq worker`` and
    ``soniq scheduler`` both resolve their modules this way so the two commands
    can't drift apart.

    Example:
        merge_module_lists("app.tasks, billing.tasks", "app.tasks")
        → ["app.tasks", "billing.tasks"]
    """
    seen: set[str] = set()
    merged: List[str] = []
    for source in sources:
        for module in parse_jobs_modules(source or ""):
            if module not in seen:
                seen.add(module)
                merged.append(module)
    return merged


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


class AmbiguousAppError(RuntimeError):
    """Raised when job modules define more than one distinct Soniq instance."""


def _scan_modules_for_instances(module_names: List[str]) -> list[tuple[str, Soniq]]:
    """Collect distinct Soniq instances defined at the top level of the given
    (already-imported) modules. Deduplicated by object identity."""
    from soniq import Soniq  # local import to avoid an import cycle

    found: list[tuple[str, Soniq]] = []
    seen_ids: set[int] = set()
    for name in module_names:
        module = sys.modules.get(name)
        if module is None:
            continue
        try:
            members = list(vars(module).items())
        except Exception:  # pragma: no cover - exotic module objects
            continue
        for attr_name, value in members:
            if isinstance(value, Soniq) and id(value) not in seen_ids:
                seen_ids.add(id(value))
                found.append((f"{name}.{attr_name}", value))
    return found


def _has_registered_jobs(app: Soniq) -> bool:
    """True when the instance has at least one job in its registry.

    Used to break resolution ties: the instance the job modules actually
    registered handlers on is the one a worker/scheduler should run.
    """
    try:
        return bool(app.registry.list_jobs())
    except Exception:  # pragma: no cover - defensive against custom registries
        return False


def _resolve_single(found: list[tuple[str, Soniq]]) -> Soniq:
    """Pick the one owning instance from candidates, or raise if truly ambiguous.

    A single candidate wins outright. When several are found we prefer the one
    that actually has jobs registered - a second, job-less ``Soniq()`` sitting in
    the same package (a dashboard sub-app, a test fixture) shouldn't make
    resolution fail. Only when the tie can't be broken that way do we raise.
    """
    if len(found) == 1:
        return found[0][1]
    with_jobs = [(name, inst) for name, inst in found if _has_registered_jobs(inst)]
    if len(with_jobs) == 1:
        return with_jobs[0][1]
    _raise_ambiguous(with_jobs or found)


def find_soniq_app(module_paths: List[str]) -> Optional[Soniq]:
    """Find the Soniq instance the given job modules register their jobs on.

    ``@app.job()`` records the handler on the ``app`` instance's own registry,
    so the process that *runs* those jobs (the worker / scheduler) must use the
    same instance - not a freshly constructed one with an empty registry, which
    would dead-letter every job as "not registered".

    Resolution order:

    1. The instance declared at the top level of one of ``module_paths`` (the
       modules named in ``SONIQ_JOBS_MODULES``). This is the explicit entry
       point and wins when present.
    2. Otherwise, the single instance found in a sibling module of the listed
       modules' own top-level packages. This covers the common layout where the
       job modules only *import* the handlers and the ``Soniq`` instance lives
       in a sibling module (e.g. ``myapp.jobs`` importing from ``myapp.app``).

    When either step finds more than one instance, the one with jobs registered
    wins; a tie it can't break that way raises ``AmbiguousAppError``.

    Args:
        module_paths: Dotted module paths passed to
            ``discover_and_import_modules`` (already imported).

    Returns:
        The Soniq instance, or ``None`` if none is defined in the import graph.

    Raises:
        AmbiguousAppError: if resolution finds more than one distinct instance
            and cannot tell which one owns the jobs.
    """
    direct = _scan_modules_for_instances(module_paths)
    if direct:
        return _resolve_single(direct)

    # Fall back to the rest of the listed modules' own top-level packages - the
    # instance may live in a sibling module the job modules import (e.g.
    # ``myapp.jobs`` importing from ``myapp.app``). Bound the scan to those
    # packages so an unrelated Soniq instance elsewhere in the process can't
    # make resolution ambiguous.
    top_packages = {name.split(".", 1)[0] for name in module_paths}
    sibling_names = [
        name
        for name in list(sys.modules.keys())
        if name.split(".", 1)[0] in top_packages and name not in module_paths
    ]
    siblings = _scan_modules_for_instances(sibling_names)
    if not siblings:
        return None
    return _resolve_single(siblings)


def _raise_ambiguous(found: list[tuple[str, Soniq]]) -> NoReturn:
    locations = ", ".join(name for name, _ in found)
    raise AmbiguousAppError(
        f"Multiple Soniq instances found ({locations}). A worker can only run "
        "one instance's jobs. Consolidate to a single shared Soniq instance, "
        "or point SONIQ_JOBS_MODULES at only the module that defines the one "
        "you want."
    )
