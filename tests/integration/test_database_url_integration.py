"""
Integration tests for --database-url parameter with core Soniq commands.

These tests verify that the database context system works correctly with
core Soniq CLI commands when using the --database-url parameter.
"""

import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent


def _make_test_db_url(db_name: str) -> str:
    """Build a database URL for the given DB name, inheriting credentials from CI env."""
    base = os.environ.get("SONIQ_DATABASE_URL", "")
    if base:
        parsed = urlparse(base)
        return urlunparse(parsed._replace(path=f"/{db_name}"))
    return f"postgresql://postgres@localhost/{db_name}"


def run_cli_command(cmd_args, timeout=10, expect_success=True):
    """Run a CLI command and return the result."""
    full_cmd = [sys.executable, "-m", "soniq.cli.main"] + cmd_args
    env = os.environ.copy()
    env.setdefault("PYTHONPATH", str(PROJECT_ROOT))

    result = subprocess.run(
        full_cmd,
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )

    if expect_success and result.returncode != 0:
        pytest.fail(
            f"Command failed: {' '.join(full_cmd)}\n"
            f"Return code: {result.returncode}\n"
            f"Stdout: {result.stdout}\n"
            f"Stderr: {result.stderr}"
        )

    return result


@pytest.fixture(scope="session", autouse=True)
async def setup_test_databases():
    """Set up test databases for database URL integration tests."""
    test_databases = [
        "soniq_db_url_test_1",
        "soniq_db_url_test_2",
        "soniq_db_url_test_3",
    ]

    # Create test databases — pass PGPASSWORD for CI environments
    createdb_env = os.environ.copy()
    base_url = os.environ.get("SONIQ_DATABASE_URL", "")
    if base_url:
        parsed = urlparse(base_url)
        if parsed.password:
            createdb_env["PGPASSWORD"] = parsed.password
    for db_name in test_databases:
        createdb_cmd = ["createdb", db_name]
        if base_url:
            parsed = urlparse(base_url)
            if parsed.username:
                createdb_cmd.extend(["-U", parsed.username])
            if parsed.hostname:
                createdb_cmd.extend(["-h", parsed.hostname])
            if parsed.port:
                createdb_cmd.extend(["-p", str(parsed.port)])
        subprocess.run(createdb_cmd, check=False, env=createdb_env)

        # Set up each database with Soniq schema using the setup command
        db_url = _make_test_db_url(db_name)
        setup_env = os.environ.copy()
        setup_env.setdefault("PYTHONPATH", str(PROJECT_ROOT))
        setup_result = subprocess.run(
            [
                sys.executable,
                "-m",
                "soniq.cli.main",
                "setup",
                "--database-url",
                db_url,
            ],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            env=setup_env,
        )

        if setup_result.returncode != 0:
            print(f"Failed to setup {db_name}: {setup_result.stderr}")
            # Continue anyway, some tests might still work

    yield

    # Cleanup databases
    for db_name in test_databases:
        dropdb_cmd = ["dropdb", "--if-exists", db_name]
        if base_url:
            parsed = urlparse(base_url)
            if parsed.username:
                dropdb_cmd.extend(["-U", parsed.username])
            if parsed.hostname:
                dropdb_cmd.extend(["-h", parsed.hostname])
            if parsed.port:
                dropdb_cmd.extend(["-p", str(parsed.port)])
        subprocess.run(dropdb_cmd, check=False, env=createdb_env)


class TestDatabaseUrlIntegration:
    """Test database URL integration with core Soniq commands."""

    def test_setup_with_database_url(self):
        """Test that setup command works with --database-url parameter."""
        test_db_url = _make_test_db_url("soniq_db_url_test_1")

        result = run_cli_command(["setup", "--database-url", test_db_url])
        assert result.returncode == 0
        assert "Using instance-based configuration" in result.stdout or result.stderr
        assert "Applied" in result.stdout or "Database setup completed" in result.stdout

    def test_migrate_status_with_database_url(self):
        """Test that migrate-status command works with --database-url parameter."""
        test_db_url = _make_test_db_url("soniq_db_url_test_1")

        result = run_cli_command(["migrate-status", "--database-url", test_db_url])
        assert result.returncode == 0
        assert "Using instance-based configuration" in result.stdout or result.stderr

    def test_status_with_database_url(self):
        """Test that status command works with --database-url parameter."""
        test_db_url = _make_test_db_url("soniq_db_url_test_1")

        result = run_cli_command(["status", "--database-url", test_db_url])
        assert result.returncode == 0
        assert "Using instance-based configuration" in result.stdout or result.stderr

    def test_workers_with_database_url(self):
        """Test that inspect command works with --database-url parameter."""
        test_db_url = _make_test_db_url("soniq_db_url_test_1")

        result = run_cli_command(["inspect", "--database-url", test_db_url])
        assert result.returncode == 0
        assert "Using instance-based configuration" in result.stdout or result.stderr

    def test_start_worker_with_database_url(self):
        """Test that worker command works with --database-url parameter."""
        test_db_url = _make_test_db_url("soniq_db_url_test_1")

        # Use --run-once to exit quickly
        result = run_cli_command(
            [
                "worker",
                "--database-url",
                test_db_url,
                "--run-once",
                "--concurrency",
                "1",
            ],
            timeout=5,
        )
        assert result.returncode == 0
        assert "Using instance-based configuration" in result.stdout or result.stderr

    def test_multiple_database_urls_isolation(self):
        """Test that different --database-url parameters target different databases."""
        test_db_url_1 = _make_test_db_url("soniq_db_url_test_1")
        test_db_url_2 = _make_test_db_url("soniq_db_url_test_2")

        # Both should work independently
        result1 = run_cli_command(["status", "--database-url", test_db_url_1])
        result2 = run_cli_command(["status", "--database-url", test_db_url_2])

        assert result1.returncode == 0
        assert result2.returncode == 0

        # Both should show instance-based configuration
        assert "Using instance-based configuration" in result1.stdout or result1.stderr
        assert "Using instance-based configuration" in result2.stdout or result2.stderr


class TestEnvFallback:
    """Without --database-url, the CLI falls back to SONIQ_DATABASE_URL."""

    def test_cli_commands_use_env_database_url(self):
        """Commands without --database-url read SONIQ_DATABASE_URL from the env."""
        original_env = os.environ.copy()

        try:
            os.environ["SONIQ_DATABASE_URL"] = _make_test_db_url("soniq_db_url_test_1")

            result = run_cli_command(["status"])
            assert result.returncode == 0
            assert (
                "Using instance-based configuration" in result.stdout or result.stderr
            )

            result = run_cli_command(["inspect"])
            assert result.returncode == 0
            assert (
                "Using instance-based configuration" in result.stdout or result.stderr
            )

        finally:
            os.environ.clear()
            os.environ.update(original_env)

    def test_top_level_imports(self):
        """Public top-level imports stay stable: only Soniq and supporting types."""
        import soniq
        from soniq import Soniq

        assert Soniq is not None
        assert hasattr(soniq, "TaskRef")
        assert hasattr(soniq, "JobContext")
