"""
Tests that all migration SQL files use TIMESTAMP WITH TIME ZONE consistently.
"""

from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent.parent.parent / "soniq" / "db" / "migrations"


def test_all_migrations_use_timestamptz():
    """
    Every CREATE TABLE column that uses TIMESTAMP must use TIMESTAMP WITH TIME ZONE.
    Bare TIMESTAMP (without timezone) is not allowed because it creates an
    inconsistency trap for users querying the database directly.
    """
    violations = []

    for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
        content = sql_file.read_text()
        lines = content.splitlines()

        for i, line in enumerate(lines, 1):
            stripped = line.strip().upper()
            # Skip ALTER lines (migration 005 converts existing columns)
            if "ALTER" in stripped:
                continue
            # Skip comments
            if stripped.startswith("--"):
                continue
            # Look for bare TIMESTAMP that is NOT followed by WITH TIME ZONE
            if "TIMESTAMP" in stripped and "WITH TIME ZONE" not in stripped:
                # Skip index definitions and other non-column-type usage
                if "CREATE INDEX" in stripped or "INDEX" in stripped:
                    continue
                # Fail on bare TIMESTAMP used as a column type
                if (
                    "TIMESTAMP DEFAULT" in stripped
                    or "TIMESTAMP," in stripped
                    or stripped.endswith("TIMESTAMP")
                    or stripped.endswith("TIMESTAMP)")
                ):
                    violations.append(f"{sql_file.name}:{i}: {line.strip()}")

    assert violations == [], (
        "Found bare TIMESTAMP columns (should be TIMESTAMP WITH TIME ZONE):\n"
        + "\n".join(f"  {v}" for v in violations)
    )
