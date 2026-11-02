"""Migration export and hashing details that the SQL API depends on."""

from soniq.backends.postgres.migration_runner import MigrationRunner
from soniq.cli.migrations_export import _select, render_sql
from soniq.utils.hashing import compute_args_hash


def test_core_migrations_have_no_percent_sign():
    # Alembic offline mode with psycopg doubles every percent sign.
    for version, name, path in MigrationRunner().discover_migrations("00"):
        assert "%" not in path.read_text(encoding="utf-8"), f"{version}_{name}"


def test_sql_export_is_one_transaction():
    out = render_sql(_select("0005"))
    assert out.startswith("BEGIN;\n")
    assert out.endswith("COMMIT;\n")


def test_documented_hash_vector():
    args = {"to": "zoë@example.com", "order_id": 42, "tags": ["a", "b"]}
    assert compute_args_hash(args) == (
        "4679a46d38de4bf4d70793166c09d90fb32fa2f36cac5f9193000b4e17bc2c9c"
    )


def test_mixed_key_types_hash_without_error():
    assert compute_args_hash({1: "a", "b": 2}) == compute_args_hash({"1": "a", "b": 2})
