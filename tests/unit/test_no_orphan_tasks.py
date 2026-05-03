"""
Guard against orphaned asyncio tasks in the soniq package.

An orphaned `asyncio.create_task(...)` (or `asyncio.ensure_future(...)` /
`loop.create_task(...)`) call is one whose result is never stored. The task
can be garbage-collected before it finishes, and any exception it raises is
lost. Every call site in the package must either:

  1. Assign the result (`t = asyncio.create_task(...)`)
  2. Append it somewhere (`self._workers.append(asyncio.create_task(...))`)
  3. Use it as a call argument (passed into asyncio.wait / gather / etc.)
  4. Return it to the caller
  5. Be explicitly allowlisted below with justification.

This test AST-walks the package and fails on any bare expression-statement
call whose value is thrown away.
"""

import ast
import pathlib

import soniq

PACKAGE_ROOT = pathlib.Path(soniq.__file__).parent

# Modules with expression-statement task spawns we have audited and accepted.
# Empty today; add a (module_relpath, lineno) tuple with a comment below
# explaining why the orphan is safe before adding anything here.
ALLOWLIST: set[tuple[str, int]] = set()


def _is_task_spawn(node: ast.Call) -> bool:
    """Return True if the call is asyncio.create_task/ensure_future or loop.create_task."""
    func = node.func
    if isinstance(func, ast.Attribute):
        if func.attr in ("create_task", "ensure_future"):
            return True
    return False


def _collect_orphan_spawns(tree: ast.AST) -> list[int]:
    """Return line numbers of bare expression-statement task spawns."""
    orphans: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Expr):
            continue
        value = node.value
        if not isinstance(value, ast.Call):
            continue
        if _is_task_spawn(value):
            orphans.append(node.lineno)
    return orphans


def test_no_orphan_task_spawns_in_package():
    failures: list[str] = []
    for path in PACKAGE_ROOT.rglob("*.py"):
        rel = path.relative_to(PACKAGE_ROOT).as_posix()
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for lineno in _collect_orphan_spawns(tree):
            if (rel, lineno) in ALLOWLIST:
                continue
            failures.append(f"{rel}:{lineno}")

    assert not failures, (
        "Orphaned asyncio task spawn(s) found - result is discarded. "
        "Store the task, append it to a collection, or add it to the ALLOWLIST "
        "with justification:\n  " + "\n  ".join(failures)
    )
