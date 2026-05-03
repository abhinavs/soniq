"""
Tests for backends/postgres/migration_runner.py - MigrationRunner basics.
"""

from soniq.backends.postgres.migration_runner import MigrationRunner


class TestMigrationRunner:
    def test_default_migrations_dir(self):
        runner = MigrationRunner()
        assert runner.migrations_dir.exists()
        assert runner.migrations_dir.name == "migrations"

    def test_custom_migrations_dir(self, tmp_path):
        runner = MigrationRunner(migrations_dir=tmp_path)
        assert runner.migrations_dir == tmp_path

    def test_discover_migrations(self):
        """Should discover SQL migration files from the default directory."""
        runner = MigrationRunner()
        # The migrations directory should have at least one migration file
        migrations = list(runner.migrations_dir.glob("*.sql"))
        assert len(migrations) > 0
