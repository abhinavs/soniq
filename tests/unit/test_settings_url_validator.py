"""
Test that settings accepts non-PostgreSQL database URLs.
"""

from soniq.settings import SoniqSettings


def test_sqlite_url_accepted():
    """SQLite database URL should not be rejected by the validator."""
    settings = SoniqSettings(database_url="jobs.db")
    assert settings.database_url == "jobs.db"


def test_memory_url_accepted():
    """Memory URL should not be rejected."""
    settings = SoniqSettings(database_url=":memory:")
    assert settings.database_url == ":memory:"


def test_postgres_url_still_accepted():
    """Postgres URLs should still work."""
    settings = SoniqSettings(database_url="postgresql://localhost/mydb")
    assert settings.database_url == "postgresql://localhost/mydb"
