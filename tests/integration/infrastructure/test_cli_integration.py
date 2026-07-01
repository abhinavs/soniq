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
        output_file = Path("/tmp/soniq_cli_test.txt")
        if output_file.exists():
            output_file.unlink()

        result = run_cli_command(["worker", "--run-once"], timeout=10)
        assert result.returncode == 0

        # The worker must actually RUN the job, not dead-letter it as
        # "not registered". This asserts unconditionally: an earlier version of
        # this test guarded the check with `if output_file.exists()`, which
        # silently passed when the worker used a fresh instance whose registry
        # never saw the discovered job.
        assert output_file.exists(), (
            "worker did not execute the discovered job - check that the CLI "
            "runs on the job-module instance, not a fresh empty one"
        )
        content = output_file.read_text()
        assert "CLI test: Hello CLI" in content
        output_file.unlink()

    finally:
        os.environ.clear()
        os.environ.update(original_env)
        if os.path.exists(job_file_path):
            os.remove(job_file_path)


@pytest.mark.asyncio
async def test_scheduler_runs_periodic_on_discovered_instance():
    """The scheduler must import the job modules and run on the instance that
    declared the @app.periodic jobs, then actually enqueue them.

    This is the scheduler analogue of test_cli_with_real_jobs. Before the
    discovery fix the scheduler imported no job modules at all and ran on a
    fresh, empty instance, so `_register_decorated` saw zero periodic jobs and
    fired nothing. The assertion below fails against that behaviour.
    """
    original_env = os.environ.copy()

    job_file_content = """
from datetime import timedelta

from soniq import Soniq

app = Soniq()

@app.periodic(every=timedelta(seconds=1), name="sched_test_job")
async def sched_test_job():
    return "tick"
"""
    job_file_path = PROJECT_ROOT / "test_sched_jobs.py"
    with open(job_file_path, "w") as f:
        f.write(job_file_content)

    from soniq import Soniq

    proc = None
    checker = Soniq(database_url=TEST_DATABASE_URL)
    try:

        async def enqueued_count() -> int:
            jobs = await checker.list_jobs(limit=500)
            return sum(1 for j in jobs if j.get("job_name") == "sched_test_job")

        # Count what's already there so we assert on a *new* enqueue, not
        # residue left by a previous run.
        before = await enqueued_count()

        env = os.environ.copy()
        env["SONIQ_DATABASE_URL"] = TEST_DATABASE_URL
        env["SONIQ_JOBS_MODULES"] = "test_sched_jobs"
        env.setdefault("PYTHONPATH", str(PROJECT_ROOT))

        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "soniq.cli.main",
                "scheduler",
                "--check-interval",
                "1",
            ],
            cwd=str(PROJECT_ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        # The interval schedule fires ~1s after the scheduler registers it, so
        # a new job should show up within a few ticks.
        fired = False
        for _ in range(40):  # up to ~8s
            await asyncio.sleep(0.2)
            if proc.poll() is not None:
                break
            if await enqueued_count() > before:
                fired = True
                break

        proc_output = ""
        if proc.poll() is not None:
            proc_output = proc.stdout.read() if proc.stdout else ""

        assert fired, (
            "scheduler did not enqueue the @periodic job - it must import the "
            "job modules and run on the instance that declared them, not a "
            f"fresh empty one. Scheduler output:\n{proc_output}"
        )
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        await checker.close()
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
    """`--database-url` is honored by commands that build their own instance
    (setup), and accepted by the worker when it matches the job-module instance.

    Note the ``or result.stderr`` shortcut in the earlier version of this test
    made its output assertions always truthy, so it only ever really checked the
    return code. These assertions concatenate stdout+stderr and check for real
    substrings instead.
    """
    test_db_name = "soniq_cli_instance_test"
    test_db_url = make_test_db_url(test_db_name)

    run_createdb(test_db_name)

    try:
        result = run_cli_command(["setup", "--database-url", test_db_url])
        assert result.returncode == 0
        assert "Database setup completed" in result.stdout or "Applied" in result.stdout

        # The worker runs on the instance its job modules define. Passing a
        # --database-url that points at that same database is consistent, so it
        # runs cleanly. (run_cli_command sets SONIQ_DATABASE_URL to
        # TEST_DATABASE_URL, which is where the cli_jobs fixture instance
        # connects.)
        result = run_cli_command(
            ["worker", "--database-url", TEST_DATABASE_URL, "--run-once"], timeout=5
        )
        assert result.returncode == 0
        combined = result.stdout + result.stderr
        assert "Using job-module instance" in combined

    finally:
        run_dropdb(test_db_name)


@pytest.mark.asyncio
async def test_cli_worker_database_url_conflict_errors():
    """A worker runs on the instance its job modules define. If the operator
    also passes a --database-url pointing at a *different* database, that's a
    genuine conflict: the flag can't be applied to an already-built instance,
    and silently running on the instance's database would be the wrong DB with
    no signal. It must fail loudly.

    (This replaces the old test_cli_database_url_vs_environment, whose premise -
    "--database-url overrides the environment for the worker" - no longer holds
    now that the worker runs on the discovered job-module instance.)
    """
    other_db_url = make_test_db_url("soniq_cli_conflict_test")

    # SONIQ_DATABASE_URL (inherited by run_cli_command) points the discovered
    # cli_jobs instance at TEST_DATABASE_URL; --database-url points elsewhere.
    # The conflict is detected before any DB connection, so no createdb needed.
    result = run_cli_command(
        ["worker", "--database-url", other_db_url, "--run-once"],
        expect_success=False,
        timeout=5,
    )
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "--database-url" in combined
    assert "conflicts" in combined


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
