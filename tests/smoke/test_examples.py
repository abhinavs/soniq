"""
Smoke tests for example files.

These tests block regressions where an example references a deleted
public API. They run without a database: each example is compiled and
imported, but its ``main()`` is never executed.

Run with: pytest tests/smoke/ -v
"""

import ast
import importlib.util
import py_compile
from pathlib import Path

import pytest

EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"
PROJECT_ROOT = Path(__file__).parent.parent.parent

EXAMPLE_FILES = sorted(EXAMPLES_DIR.glob("*.py"))

# Examples that pull in optional third-party deps not guaranteed to be
# installed in the smoke environment. They still get compiled and
# AST-checked; only the live import is skipped.
IMPORT_SKIP = {
    "transactional_enqueue.py": "fastapi",
}


@pytest.mark.parametrize("example", EXAMPLE_FILES, ids=lambda p: p.name)
def test_example_compiles(example):
    """Every example must compile to bytecode without syntax errors."""
    py_compile.compile(str(example), doraise=True)


@pytest.mark.parametrize("example", EXAMPLE_FILES, ids=lambda p: p.name)
def test_example_imports(example):
    """Every example must import cleanly.

    Catches references to deleted public symbols at module load and
    decorator time without ever calling ``main()`` (so no DB needed).
    """
    optional_dep = IMPORT_SKIP.get(example.name)
    if optional_dep is not None:
        pytest.importorskip(optional_dep)

    spec = importlib.util.spec_from_file_location(
        f"_smoke_example_{example.stem}", example
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)


@pytest.mark.parametrize("example", EXAMPLE_FILES, ids=lambda p: p.name)
def test_example_imports_resolve(example):
    """All top-level imports in each example must resolve to real modules."""
    optional_dep = IMPORT_SKIP.get(example.name)
    if optional_dep is not None:
        pytest.importorskip(optional_dep)

    source = example.read_text()
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                __import__(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            __import__(node.module.split(".")[0])


def test_recurring_jobs_uses_real_api():
    """recurring_jobs.py must use the actual soniq API, not fictional methods."""
    source = (EXAMPLES_DIR / "recurring_jobs.py").read_text()

    assert "app.schedule(" not in source or "run_at" in source or "run_in" in source, (
        "recurring_jobs.py calls app.schedule() with a cron string, "
        "but app.schedule() requires run_at or run_in keyword arguments"
    )
    assert ").schedule(" not in source, (
        "recurring_jobs.py calls a `.schedule(...)` terminal which does not "
        "exist. Use `@app.periodic(cron=...)` or `app.scheduler.add(...)`."
    )


def test_transactional_enqueue_setup_call():
    """transactional_enqueue.py must not pass unsupported args to setup()."""
    source = (EXAMPLES_DIR / "transactional_enqueue.py").read_text()

    assert "setup(database_url=" not in source, (
        "transactional_enqueue.py passes database_url to setup(), "
        "but setup() takes no arguments"
    )


def test_no_dead_documentation_urls():
    """No references to non-existent docs.soniq.abhinav.co should exist in source code."""
    dead_url_files = []
    soniq_dir = PROJECT_ROOT / "soniq"

    for py_file in soniq_dir.rglob("*.py"):
        content = py_file.read_text()
        if "docs.soniq.abhinav.co" in content:
            dead_url_files.append(str(py_file.relative_to(PROJECT_ROOT)))

    assert (
        dead_url_files == []
    ), f"Found references to non-existent docs.soniq.abhinav.co in: {dead_url_files}"
