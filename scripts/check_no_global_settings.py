#!/usr/bin/env python3
r"""
Lint: ``get_settings()`` may only be called inside the constructor /
bootstrap allowlist.

Why this lint exists. ``docs/contracts/instance_boundary.md`` locks the
allowed process-global state to one item: the Python ``logging`` stack.
Settings are per-Soniq-instance and must come in via the constructor
or be threaded through call sites. A loose ``get_settings()`` call in a
runtime path silently re-couples that path to a process-global cache
and lets two ``Soniq(...)`` instances bleed configuration into each
other (different ``job_timeout``, different ``result_ttl``, etc.).

What it checks. Greps every ``.py`` file under ``soniq/`` for the
literal call ``get_settings(`` (the trailing paren keeps it from
matching ``get_settings`` as an attribute name or import). Lines that
sit in a comment (``#``) or that wrap the name in backticks (a doc
reference like ``\`get_settings()\`` inside a docstring) are skipped.
Any remaining hit outside the allowlist below is a CI failure with the
offending file:line printed.

Allowlist. The locked set is small: the constructor / bootstrap files
that genuinely need to read settings to build an instance.

- ``soniq/settings.py`` itself - this is where ``get_settings`` lives.
- ``soniq/core/worker.py`` - ``Worker.__init__`` resolves a default
  settings instance when one is not passed.
- ``soniq/backends/postgres/__init__.py`` - ``PostgresBackend.__init__``
  reads pool sizing defaults at construction.

Adding to the allowlist requires amending the contract first; no PR
may quietly grow this list. The script is grep-shaped (not AST) on
purpose: ``get_settings`` is a single name with one import path, the
rule fits on a screen, and an AST tool here would obscure intent.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Iterable, List, Tuple

# Files allowed to call get_settings(). Paths are relative to the repo
# root and use forward slashes regardless of platform.
ALLOWLIST = frozenset(
    {
        "soniq/settings.py",
        "soniq/core/worker.py",
        "soniq/backends/postgres/__init__.py",
    }
)

# Match `get_settings(` not preceded by a backtick (which would mark it
# as a docstring reference rather than a call).
_CALL_RE = re.compile(r"(?<!`)\bget_settings\s*\(")


def _iter_violations(path: Path, src: str) -> Iterable[Tuple[int, str]]:
    for lineno, line in enumerate(src.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        if _CALL_RE.search(line):
            yield lineno, line.rstrip()


def _gather_files(root: Path) -> List[Path]:
    base = root / "soniq"
    if not base.exists():
        return []
    return sorted(p for p in base.rglob("*.py") if p.is_file())


def main(argv: List[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    root = Path(argv[0]) if argv else Path.cwd()

    files = _gather_files(root)
    if not files:
        print(
            f"check_no_global_settings: no files under {root}/soniq - "
            "nothing to check",
            file=sys.stderr,
        )
        return 0

    failed = False
    for path in files:
        rel = path.relative_to(root) if path.is_absolute() else path
        rel_posix = rel.as_posix()
        if rel_posix in ALLOWLIST:
            continue
        src = path.read_text(encoding="utf-8")
        for lineno, snippet in _iter_violations(path, src):
            failed = True
            print(f"{rel_posix}:{lineno}: get_settings() outside allowlist: {snippet}")

    if failed:
        print(
            "\nget_settings() is a process-global cache. Per "
            "docs/contracts/instance_boundary.md it may only be called "
            "from constructor / bootstrap files in the allowlist. To "
            "thread settings through a runtime path, accept "
            "`SoniqSettings` as an arg or attach to the owning Soniq "
            "instance.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
