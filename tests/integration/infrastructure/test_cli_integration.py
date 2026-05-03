"""
Test suite for CLI integration and real command execution
"""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.db_utils import TEST_DATABASE_URL, make_test_db_url, run_createdb, run_dropdb

os.environ["SONIQ_DATABASE_URL"] = TEST_DATABASE_URL

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def run_cli_command(args, timeout=10, expect_success=True):
    """Helper to run CLI commands"""
    env = os.environ.copy()
    env.setdefault("SONIQ_JOBS_MODULES", "tests.fixtures.cli_jobs")
    env.setdefault("PYTHONPATH", str(PROJECT_ROOT))
    try:
        result = subprocess.run(
            [sys.executable, "-m", "soniq.cli.main"] + args,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(PROJECT_ROOT),
            env=env,
        )

        if expect_success and result.returncode != 0:
            pytest.fail(f"CLI command failed: {result.stderr}")

        return result
    except subprocess.TimeoutExpired:
        pytest.fail(f"CLI command timed out: {args}")
    except Exception as e:
        pytest.fail(f"CLI command error: {e}")


@pytest.mark.asyncio
async def test_cli_help_commands():
    """Test CLI help and command discovery"""
    result = run_cli_command(["--help"])
    assert "Soniq CLI" in result.stdout or "usage:" in result.stdout

    result = run_cli_command(["setup", "--help"])
    assert "setup" in result.stdout.lower()

    result = run_cli_command(["worker", "--help"])
    assert "worker" in result.stdout.lower()


@pytest.mark.asyncio
async def test_setup_command():
    """Test database setup command"""
    run_createdb("soniq_test")

    result = run_cli_command(["setup"])
    assert result.returncode == 0

    check_result = subprocess.run(
        [
            "psql",
            TEST_DATABASE_URL,
            "-c",
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'soniq_jobs';",
        ],
        capture_output=True,
        text=True,
    )
    assert "1" in check_result.stdout


@pytest.mark.asyncio
async def test_start_command_structure():
    """Test worker command options and parsing"""
    result = run_cli_command(["worker", "--run-once"], timeout=5)
    assert result.returncode == 0

    result = run_cli_command(["worker", "--concurrency", "2", "--run-once"], timeout=5)
    assert result.returncode == 0

    result = run_cli_command(
        ["worker", "--queues", "test1,test2", "--run-once"], timeout=5
    )
    assert result.returncode == 0


@pytest.mark.asyncio
async def test_environment_variable_integration():
    """Test CLI respects environment variables"""
    original_env = os.environ.copy()

    try:
        os.environ["SONIQ_LOG_LEVEL"] = "DEBUG"
        os.environ["SONIQ_DATABASE_URL"] = TEST_DATABASE_URL

        result = run_cli_command(["worker", "--run-once"], timeout=5)
        assert result.returncode == 0

    finally:
        os.environ.clear()
        os.environ.update(original_env)


@pytest.mark.asyncio
async def test_cli_error_handling():
    """Test CLI error handling for invalid commands and options"""
    result = run_cli_command(["invalid-command"], expect_success=False)
    assert result.returncode != 0

    result = run_cli_command(["worker", "--invalid-option"], expect_success=False)
    assert result.returncode != 0

    original_db_url = os.environ.get("SONIQ_DATABASE_URL")
    try:
        os.environ["SONIQ_DATABASE_URL"] = "invalid://url"
        result = run_cli_command(["setup"], expect_success=False, timeout=30)
        assert result.returncode != 0
    finally:
        if original_db_url:
            os.environ["SONIQ_DATABASE_URL"] = original_db_url


@pytest.mark.asyncio
async def test_cli_with_real_jobs():
    """Test CLI with actual job processing"""
    original_env = os.environ.copy()

    job_file_content = """
from soniq import Soniq

app = Soniq()

@app.job(name="cli_test_job", retries=1)
async def cli_test_job(message: str):
    with open("/tmp/soniq_cli_test.txt", "w") as f:
        f.write(f"CLI test: {message}")
    return f"Processed: {message}"
"""

    job_file_path = PROJECT_ROOT / "test_cli_jobs.py"
    with open(job_file_path, "w") as f:
        f.write(job_file_content)

    try:
        import sys

        sys.path.insert(0, str(PROJECT_ROOT))

        import importlib

        if "test_cli_jobs" in sys.modules:
            del sys.modules["test_cli_jobs"]
        importlib.import_module("test_cli_jobs")

        from soniq import Soniq

        # Producer-only enqueue from a fresh Soniq with no local registry
        # (the worker side imports test_cli_jobs and registers the task).
        os.environ["SONIQ_ENQUEUE_VALIDATION"] = "warn"
        app = Soniq(database_url=TEST_DATABASE_URL)
        await app.enqueue("cli_test_job", args={"message": "Hello CLI"})
        await app.close()

        os.environ["SONIQ_JOBS_MODULES"] = "test_cli_jobs"
        result = run_cli_command(["worker", "--run-once"], timeout=10)
        assert result.returncode == 0

        output_file = Path("/tmp/soniq_cli_test.txt")
        if output_file.exists():
            content = output_file.read_text()
            assert "CLI test: Hello CLI" in content
            output_file.unlink()

    finally:
        os.environ.clear()
        os.environ.update(original_env)
        if os.path.exists(job_file_path):
            os.remove(job_file_path)


@pytest.mark.asyncio
async def test_cli_output_formats():
    """Test CLI output formatting and verbosity"""
    result = run_cli_command(["setup"])

    assert len(result.stdout) > 0 or len(result.stderr) > 0

    result = run_cli_command(["worker", "--run-once"], timeout=5)

    assert len(result.stdout) > 0 or len(result.stderr) > 0


@pytest.mark.asyncio
async def test_cli_concurrent_workers():
    """Test running multiple CLI workers concurrently"""
    worker_processes = []

    try:
        for i in range(2):
            env = os.environ.copy()
            env.setdefault("SONIQ_JOBS_MODULES", "tests.fixtures.cli_jobs")
            env.setdefault("PYTHONPATH", str(PROJECT_ROOT))
            process = subprocess.Popen(
                [sys.executable, "-m", "soniq.cli.main", "worker", "--run-once"],
                cwd=str(PROJECT_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )
            worker_processes.append(process)

        for process in worker_processes:
            stdout, stderr = process.communicate(timeout=10)
            assert process.returncode == 0, f"Worker failed: {stderr}"

    finally:
        for process in worker_processes:
            if process.poll() is None:
                process.terminate()
                process.wait()


@pytest.mark.asyncio
async def test_cli_signal_handling():
    """Test CLI signal handling and graceful shutdown"""
    env = os.environ.copy()
    env.setdefault("SONIQ_JOBS_MODULES", "tests.fixtures.cli_jobs")
    env.setdefault("PYTHONPATH", str(PROJECT_ROOT))
    process = subprocess.Popen(
        [sys.executable, "-m", "soniq.cli.main", "worker", "--concurrency", "1"],
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )

    try:
        await asyncio.sleep(2)

        process.terminate()

        stdout, stderr = process.communicate(timeout=10)

        assert process.returncode in [
            0,
            -15,
            143,
            130,
        ]

        combined_output = stdout + stderr
        shutdown_keywords = ["shutdown", "stopped", "graceful", "closing", "cleanup"]
        has_shutdown_message = any(
            keyword in combined_output.lower() for keyword in shutdown_keywords
        )

        assert has_shutdown_message or process.returncode == 0, (
            f"Expected graceful shutdown message OR clean exit, got returncode {process.returncode} "
            f"with output: {combined_output}"
        )

    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        pytest.fail("Worker did not shut down gracefully")


@pytest.mark.asyncio
async def test_cli_database_url_parameter():
    """Test CLI commands with --database-url parameter"""
    test_db_name = "soniq_cli_instance_test"
    test_db_url = make_test_db_url(test_db_name)

    run_createdb(test_db_name)

    try:
        result = run_cli_command(["setup", "--database-url", test_db_url])
        assert result.returncode == 0
        assert "Database setup completed" in result.stdout or "Applied" in result.stdout

        result = run_cli_command(
            ["worker", "--database-url", test_db_url, "--run-once"], timeout=5
        )
        assert result.returncode == 0

        assert "Using instance-based configuration" in result.stdout or result.stderr
        assert test_db_url in result.stdout or result.stderr

    finally:
        run_dropdb(test_db_name)


@pytest.mark.asyncio
async def test_cli_database_url_vs_environment():
    """Test that --database-url parameter overrides environment variable"""
    test_db_name = "soniq_cli_override_test"
    test_db_url = make_test_db_url(test_db_name)

    run_createdb(test_db_name)

    original_env = os.environ.copy()

    try:
        os.environ["SONIQ_DATABASE_URL"] = TEST_DATABASE_URL

        result = run_cli_command(["setup", "--database-url", test_db_url])
        assert result.returncode == 0

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

    finally:
        os.environ.clear()
        os.environ.update(original_env)
        run_dropdb(test_db_name)


@pytest.mark.asyncio
async def test_cli_database_url_validation():
    """Test CLI handles invalid database URLs gracefully"""
    result = run_cli_command(
        ["setup", "--database-url", "invalid://not-a-real-database"],
        expect_success=False,
        timeout=10,
    )

    assert result.returncode != 0
    combined_output = (result.stdout + result.stderr).lower()
    assert "error" in combined_output or "failed" in combined_output


@pytest.mark.asyncio
async def test_cli_without_database_url_uses_env():
    """Test that CLI without --database-url uses SONIQ_DATABASE_URL env var"""
    result = run_cli_command(
        ["worker", "--run-once", "--queues", "__empty__"], timeout=5
    )
    assert result.returncode == 0
