#!/usr/bin/env python3
"""
Lint: ``soniq/features/`` and ``soniq/dashboard/`` may only call the
public Soniq API.

Why this lint exists. The plan (section N) commits Soniq to keeping
first-party features under the same constraint a third-party plugin
would have to follow: no reaching for ``app._private`` or importing
``soniq._foo``. The discipline does two things at once. First, it
forces missing public API to surface as a build failure inside our
own codebase before a third-party plugin author trips over it.
Second, it catches "this feature is more coupled than the public API
admits" - if a feature finds itself wanting ``app.backend._pool``,
either we widen the public surface or we admit the coupling and
push the code somewhere it isn't pretending to be optional.

What it checks. AST-walks every ``.py`` file under ``soniq/features/``
and ``soniq/dashboard/`` and fails on:

1. ``from soniq._foo import ...`` or ``from soniq.foo._bar import ...``
   - any import path with an underscore-prefixed component below the
   ``soniq`` root.
2. ``import soniq._foo`` - same shape, ``import`` form.
3. Attribute access ``<something>._foo`` where ``<something>`` looks
   like a Soniq object (a name bound to ``app``, ``soniq_app``,
   ``self._app``, ``self.app``, etc.). The detection is intentionally
   conservative: a name has to look soniq-shaped for the lint to fire,
   so utility code like ``self._lock.acquire()`` inside a feature is
   not flagged. The conservative form catches every actual violation
   the codebase has had historically.

The script returns 0 on a clean tree and prints one line per finding
with a non-zero exit code so pre-commit treats it as a fail.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Iterable, List, Tuple

# Names we treat as "definitely a Soniq object". Any attribute access
# of the form ``<name>._foo`` where ``<name>`` is in this set is flagged
# as a public-API violation.
SONIQ_NAMES = frozenset(
    {
        "app",
        "soniq_app",
        "_app",
        "soniq",
    }
)

# ``self.<attr>._foo`` is also flagged when ``<attr>`` is one of these
# (the standard names a feature service binds the soniq instance to).
SELF_SONIQ_ATTRS = frozenset(
    {
        "_app",
        "app",
        "_soniq",
        "soniq",
    }
)


def _is_private_name(name: str) -> bool:
    """Return True for names like ``_foo``. Dunder names (``__init__``,
    ``__aenter__``) stay allowed: they are public protocol names.
    """
    return name.startswith("_") and not (name.startswith("__") and name.endswith("__"))


def _is_soniq_module(module: str) -> bool:
    """``module`` here is the dotted import path. The lint only triggers
    inside the ``soniq.*`` namespace - third-party imports with leading
    underscores (``_pytest._whatever``) are off-topic for this rule.
    """
    if not module:
        return False
    parts = module.split(".")
    if parts[0] != "soniq":
        return False
    return any(_is_private_name(p) for p in parts[1:])


def _walk(tree: ast.AST) -> Iterable[ast.AST]:
    yield from ast.walk(tree)


def _check_file(path: Path) -> List[Tuple[int, str]]:
    """Return a list of ``(lineno, message)`` findings for ``path``."""
    findings: List[Tuple[int, str]] = []
    src = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError as e:
        findings.append((e.lineno or 0, f"syntax error: {e.msg}"))
        return findings

    for node in _walk(tree):
        # `from soniq._foo import ...` / `from soniq.foo._bar import ...`
        if isinstance(node, ast.ImportFrom) and _is_soniq_module(node.module or ""):
            findings.append(
                (
                    node.lineno,
                    f"private soniq import: 'from {node.module} import ...'",
                )
            )

        # `import soniq._foo`
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_soniq_module(alias.name):
                    findings.append(
                        (
                            node.lineno,
                            f"private soniq import: 'import {alias.name}'",
                        )
                    )

        # attribute access like `app._something`, `self._app._something`
        if isinstance(node, ast.Attribute) and _is_private_name(node.attr):
            target = node.value
            # `app._x`
            if isinstance(target, ast.Name) and target.id in SONIQ_NAMES:
                findings.append(
                    (
                        node.lineno,
                        f"private attribute access: '{target.id}.{node.attr}'",
                    )
                )
            # `self._app._x` / `self.app._x`
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
                and target.attr in SELF_SONIQ_ATTRS
            ):
                findings.append(
                    (
                        node.lineno,
                        f"private attribute access: 'self.{target.attr}.{node.attr}'",
                    )
                )

    return findings


def _gather_files(root: Path) -> List[Path]:
    files: List[Path] = []
    for sub in ("features", "dashboard"):
        base = root / "soniq" / sub
        if not base.exists():
            continue
        files.extend(p for p in base.rglob("*.py") if p.is_file())
    return sorted(files)


def main(argv: List[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    root = Path(argv[0]) if argv else Path.cwd()

    files = _gather_files(root)
    if not files:
        print(
            f"lint_features_public_api: no files under "
            f"{root}/soniq/features|dashboard - nothing to check",
            file=sys.stderr,
        )
        return 0

    failed = False
    for path in files:
        for lineno, msg in _check_file(path):
            failed = True
            rel = path.relative_to(root) if path.is_absolute() else path
            print(f"{rel}:{lineno}: {msg}")

    if failed:
        print(
            "\nfeatures/ and dashboard/ may only use Soniq's public API. "
            "If you genuinely need a private name, that's a signal the "
            "public surface is missing a method - widen it instead.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
