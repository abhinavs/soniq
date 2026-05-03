"""
Tests that the migration files are correctly structured and contain all required schema.
"""

from pathlib import Path

from soniq.backends.postgres.migration_runner import MigrationRunner

MIGRATIONS_DIR = (
    Path(__file__).parent.parent.parent
    / "soniq"
    / "backends"
    / "postgres"
    / "migrations"
)


class TestMigrationStructure:
    """Verify the split migration set is laid out correctly.

    Numbering convention (see migrations/README.md):
        0001-0099  core (always applied)
        0100-8999  reserved for OSS plugins
        9000-9999  reserved for first-party commercial / soniq-pro
    """

    def test_expected_migrations_exist(self):
        files = sorted(f.name for f in MIGRATIONS_DIR.glob("*.sql"))
        assert files == [
            "0001_core.sql",
            "0002_dead_letter.sql",
            "0003_scheduler.sql",
            "0004_webhooks.sql",
            "0005_logs.sql",
        ]

    def test_migrations_discovered_in_order(self):
        runner = MigrationRunner()
        migrations = runner.discover_migrations()
        versions = [v for v, _, _ in migrations]
        assert versions == [
            "0001",
            "0002",
            "0003",
            "0004",
            "0005",
        ]

    def test_version_filter_core(self):
        runner = MigrationRunner()
        # In 0.0.2+ all soniq-owned tables (DLQ, scheduler, webhooks,
        # logs) ship in the core slice so dashboard/metrics queries can
        # reference them unconditionally.
        versions = [v for v, _, _ in runner.discover_migrations(version_filter="000")]
        assert versions == [
            "0001",
            "0002",
            "0003",
            "0004",
            "0005",
        ]


class TestCoreContents:
    """0001_core.sql carries jobs, workers, producer_id, task registry."""

    def test_jobs_and_indexes(self):
        content = (MIGRATIONS_DIR / "0001_core.sql").read_text()
        assert "soniq_jobs" in content
        assert "idx_soniq_jobs_queue_status_priority" in content
        assert "result JSONB" in content

    def test_workers_and_fk(self):
        content = (MIGRATIONS_DIR / "0001_core.sql").read_text()
        assert "soniq_workers" in content
        assert "worker_id" in content
        assert "ON DELETE SET NULL" in content

    def test_producer_id(self):
        content = (MIGRATIONS_DIR / "0001_core.sql").read_text()
        assert "producer_id TEXT" in content

    def test_task_registry(self):
        content = (MIGRATIONS_DIR / "0001_core.sql").read_text()
        assert "soniq_task_registry" in content
        assert "PRIMARY KEY (task_name, worker_id)" in content

    def test_dead_schema_dropped(self):
        # soniq_job_timeouts and soniq_config never landed in production
        # use; the 0.0.2 reset drops them entirely.
        for f in MIGRATIONS_DIR.glob("*.sql"):
            content = f.read_text()
            assert "soniq_job_timeouts" not in content, f
            assert "soniq_config" not in content, f


class TestFeatureContents:
    """Per-feature migrations carry exactly the table(s) the feature owns."""

    def test_dead_letter(self):
        content = (MIGRATIONS_DIR / "0002_dead_letter.sql").read_text()
        assert "soniq_dead_letter_jobs" in content

    def test_scheduler(self):
        content = (MIGRATIONS_DIR / "0003_scheduler.sql").read_text()
        assert "soniq_recurring_jobs" in content

    def test_webhooks(self):
        content = (MIGRATIONS_DIR / "0004_webhooks.sql").read_text()
        assert "soniq_webhook_endpoints" in content
        assert "soniq_webhook_deliveries" in content

    def test_logs(self):
        content = (MIGRATIONS_DIR / "0005_logs.sql").read_text()
        assert "soniq_logs" in content
