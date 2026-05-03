"""``soniq tasks-check`` - drift check between TaskRef stubs and the
shared registry table.

Compares the TaskRef declarations in a stub package against the
soniq_task_registry table populated by running workers. Drift exits
non-zero so CI can block deploys.

Note: the historical ``soniq tasks-list`` command was removed in 0.0.2.
With per-instance registries (no process-global Soniq), there is no
"current process registry" - the registry belongs to a Soniq instance,
and the dashboard already exposes the fleet-wide
``soniq_task_registry`` table for the same purpose.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import os
import pkgutil
import sys
from typing import Any, Dict, List, Optional

from soniq.app import Soniq
from soniq.task_ref import TaskRef

from ._helpers import database_url_argument


def add_tasks_cmd(subparsers) -> None:
    """Register ``tasks-check`` (the only remaining tasks subcommand)."""
    check_parser = subparsers.add_parser(
        "tasks-check",
        help="Compare stub-package TaskRefs against the shared registry table",
        description=(
            "Compares stub-package TaskRefs against the shared registry "
            "table populated by running workers. Drift exits non-zero so CI "
            "can block deploys. Requires SONIQ_DATABASE_URL to read the "
            "registry table."
        ),
    )
    check_parser.add_argument(
        "package",
        nargs="?",
        help=("Stub package path or dotted module containing TaskRef declarations"),
    )
    database_url_argument(check_parser)
    check_parser.set_defaults(func=handle_tasks_check)


def _load_task_refs_from_package(package_path: str) -> List[Dict[str, Any]]:
    """Import a Python package directory or module and collect TaskRef
    instances declared inside it. Returns dicts with name, args_model,
    and default_queue."""
    abs_path = os.path.abspath(package_path)
    if os.path.isdir(abs_path):
        parent = os.path.dirname(abs_path)
        package_name = os.path.basename(abs_path)
        if parent not in sys.path:
            sys.path.insert(0, parent)
        module = importlib.import_module(package_name)
    else:
        module = importlib.import_module(package_path)

    found: List[Dict[str, Any]] = []
    seen_names: set[str] = set()

    def visit(mod):
        for _, value in inspect.getmembers(mod):
            if isinstance(value, TaskRef) and value.name not in seen_names:
                found.append(
                    {
                        "name": value.name,
                        "args_model": (
                            getattr(value.args_model, "__name__", None)
                            if value.args_model
                            else None
                        ),
                        "default_queue": value.default_queue,
                    }
                )
                seen_names.add(value.name)

    visit(module)
    if hasattr(module, "__path__"):
        for info in pkgutil.iter_modules(module.__path__, prefix=f"{module.__name__}."):
            try:
                visit(importlib.import_module(info.name))
            except Exception as e:
                print(
                    f"soniq tasks-check: skipped {info.name}: {e}",
                    file=sys.stderr,
                )
    return found


async def _load_registry_table_names(database_url: Optional[str]) -> List[str]:
    """Fetch the task names registered in the soniq_task_registry table."""
    app = Soniq(database_url=database_url) if database_url else Soniq()
    await app._ensure_initialized()
    try:
        backend = app.backend
        assert backend is not None
        rows = await backend.list_registered_task_names()
        return sorted({r["task_name"] for r in rows})
    finally:
        await app.close()


def handle_tasks_check(args) -> int:
    """Compare stub-package TaskRefs against the registry table.

    Drift exits non-zero so CI can block deploys.
    """
    if not args.package:
        print(
            "soniq tasks-check: a stub package path or dotted module is required",
            file=sys.stderr,
        )
        return 2

    db_url = os.environ.get("SONIQ_DATABASE_URL") or args.database_url
    if not db_url:
        print(
            "soniq tasks-check: requires SONIQ_DATABASE_URL to read the "
            "shared task registry; set it in the environment or pass "
            "--database-url.",
            file=sys.stderr,
        )
        return 2

    refs = _load_task_refs_from_package(args.package)
    ref_names = {r["name"] for r in refs}

    table_names = set(asyncio.run(_load_registry_table_names(db_url)))

    in_stub_not_table = sorted(ref_names - table_names)
    in_table_not_stub = sorted(table_names - ref_names)

    drift_count = len(in_stub_not_table) + len(in_table_not_stub)

    if not drift_count:
        print(
            f"soniq tasks-check: OK - {len(ref_names)} TaskRef(s) match "
            f"{len(table_names)} registered name(s) in the soniq_task_registry "
            "table.",
            file=sys.stdout,
        )
        return 0

    if in_stub_not_table:
        print(
            "DRIFT: TaskRefs in the stub package have no worker registered "
            "for them in soniq_task_registry:",
            file=sys.stderr,
        )
        for n in in_stub_not_table:
            print(f"  - {n}", file=sys.stderr)
    if in_table_not_stub:
        print(
            "DRIFT: registered names in soniq_task_registry have no "
            "TaskRef in the stub package:",
            file=sys.stderr,
        )
        for n in in_table_not_stub:
            print(f"  - {n}", file=sys.stderr)
    return 2
