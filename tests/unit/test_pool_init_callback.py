"""
Tests that the instance pool uses an init callback for UTC timezone,
and that the processor does NOT set timezone per-query.
"""

import ast
from pathlib import Path


def test_postgres_backend_pool_has_init_callback():
    """PostgresBackend must pass init= to create_pool."""
    backend_path = (
        Path(__file__).parent.parent.parent
        / "soniq"
        / "backends"
        / "postgres"
        / "__init__.py"
    )
    source = backend_path.read_text()
    tree = ast.parse(source)

    found_init = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute) and node.func.attr == "create_pool":
                for kw in node.keywords:
                    if kw.arg == "init":
                        found_init = True
                        break

    assert found_init, (
        "asyncpg.create_pool() in PostgresBackend must include init= callback "
        "for connection-level UTC timezone initialization"
    )


def test_processor_does_not_set_timezone_per_query():
    """_fetch_and_lock_job must NOT call SET timezone per-query."""
    processor_path = (
        Path(__file__).parent.parent.parent / "soniq" / "core" / "processor.py"
    )
    source = processor_path.read_text()

    assert "SET timezone" not in source, (
        "processor.py should not SET timezone per-query. "
        "UTC should be set via pool init callback."
    )
