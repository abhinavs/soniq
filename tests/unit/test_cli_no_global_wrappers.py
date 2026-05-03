"""
Lint-style guard that CLI subcommands route through ``cli_app`` and do
not reintroduce process-global wrappers on top of ``soniq``.

The instance-boundary contract (`docs/_internals/contracts/instance_boundary.md`)
requires every CLI subcommand to resolve an explicit Soniq via
``cli_app(args)`` and then call methods on that instance. The legacy
module-level wrappers (``soniq.enqueue``, ``soniq.run_worker``, ...)
were removed in 0.0.2; this test fails fast if any CLI file tries to
reach for one again.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# Names that, if added back to soniq/__init__.py and called from a CLI
# subcommand, would silently reintroduce process-global state.
FORBIDDEN_GLOBAL_WRAPPERS = frozenset(
    {
        "enqueue",
        "schedule",
        "run_worker",
        "setup",
        "get_job",
        "get_result",
        "cancel_job",
        "delete_job",
        "list_jobs",
        "get_queue_stats",
        "configure",
        "get_global_app",
    }
)

ALLOWLIST: frozenset[str] = frozenset()

CLI_DIR = Path(__file__).resolve().parents[2] / "soniq" / "cli"


def _iter_subcommand_files():
    for path in sorted(CLI_DIR.glob("*.py")):
        # Skip dunders, helpers, the dispatcher, and color utilities -
        # only subcommand handlers are subject to this rule.
        if path.name.startswith("_"):
            continue
        if path.name in {"main.py", "colors.py"}:
            continue
        if path.name in ALLOWLIST:
            continue
        yield path


def _find_violations(source: str) -> list[str]:
    """Return human-readable descriptions of forbidden uses."""
    tree = ast.parse(source)
    violations: list[str] = []

    for node in ast.walk(tree):
        # Pattern 1: `from soniq import <wrapper>`
        if isinstance(node, ast.ImportFrom) and node.module == "soniq":
            for alias in node.names:
                if alias.name in FORBIDDEN_GLOBAL_WRAPPERS:
                    violations.append(
                        f"line {node.lineno}: from soniq import {alias.name}"
                    )

        # Pattern 2: `soniq.<wrapper>` attribute access (covers both
        # `import soniq` and `import soniq as _soniq` aliases).
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.attr in FORBIDDEN_GLOBAL_WRAPPERS
        ):
            # We can't resolve the alias target without symbol tracking,
            # but in practice CLI files only alias the soniq package as
            # ``soniq`` or ``_soniq``, so flag both.
            if node.value.id in {"soniq", "_soniq"}:
                violations.append(
                    f"line {node.lineno}: {node.value.id}.{node.attr}(...)"
                )

    return violations


@pytest.mark.parametrize(
    "path",
    list(_iter_subcommand_files()),
    ids=lambda p: p.name,
)
def test_cli_subcommand_does_not_call_global_wrappers(path: Path) -> None:
    violations = _find_violations(path.read_text())
    assert not violations, (
        f"{path.relative_to(CLI_DIR.parent.parent)} must route through "
        f"cli_app(args) and call methods on the resolved Soniq instance. "
        f"Forbidden uses found:\n  " + "\n  ".join(violations)
    )
