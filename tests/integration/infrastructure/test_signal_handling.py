"""
Tests for signal handling and graceful worker shutdown.

Tests signal handling for SIGTERM, SIGINT, and SIGHUP across instance API workers.
"""

import asyncio
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from tests.db_utils import TEST_DATABASE_URL

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent

_INSTANCE_WORKER_SCRIPT = """
import asyncio
import os

from soniq import Soniq

async def main():
    app = Soniq(
        database_url=os.environ.get("SONIQ_DATABASE_URL", "postgresql://localhost/soniq_test")
    )

    @app.job(name="test_job")
    async def test_job():
        return "test"

    await app.run_worker(concurrency=1)

if __name__ == "__main__":
    asyncio.run(main())
"""


def _write_worker_script() -> str:
    script_path = f"/tmp/test_worker_instance_{os.getpid()}.py"
    with open(script_path, "w") as f:
        f.write(_INSTANCE_WORKER_SCRIPT)
    return script_path


@pytest.mark.asyncio
async def test_signal_handler_setup_and_cleanup():
    """Test signal handler setup and cleanup in isolation"""
    from soniq.utils.signals import GracefulSignalHandler

    handler = GracefulSignalHandler()
    shutdown_event = asyncio.Event()

    handler.setup_signal_handlers(shutdown_event)

    assert len(handler.handled_signals) > 0
    assert signal.SIGINT in handler.handled_signals

    handler.restore_signal_handlers()

    assert len(handler.handled_signals) == 0
    assert len(handler.original_handlers) == 0
    assert handler.shutdown_event is None


@pytest.mark.asyncio
async def test_signal_handler_triggers_event():
    """Test that signal handlers properly trigger shutdown event"""
    from soniq.utils.signals import GracefulSignalHandler

    handler = GracefulSignalHandler()
    shutdown_event = asyncio.Event()

    try:
        handler.setup_signal_handlers(shutdown_event)

        os.kill(os.getpid(), signal.SIGINT)

        await asyncio.wait_for(shutdown_event.wait(), timeout=2.0)

        assert shutdown_event.is_set()

    finally:
        handler.restore_signal_handlers()


@pytest.mark.asyncio
async def test_global_signal_handlers():
    """Test global signal handler utilities"""
    from soniq.utils.signals import (
        cleanup_global_signal_handlers,
        setup_global_signal_handlers,
    )

    shutdown_event = asyncio.Event()

    try:
        handler = setup_global_signal_handlers(shutdown_event)
        assert handler is not None

        assert len(handler.handled_signals) > 0

    finally:
        cleanup_global_signal_handlers()


class TestWorkerSignalHandling:
    """Test signal handling in actual worker processes"""

    @pytest.mark.asyncio
    async def test_sigint_graceful_shutdown(self):
        """Test SIGINT (Ctrl+C) graceful shutdown for instance API worker"""
        script_path = _write_worker_script()

        try:
            process = subprocess.Popen(
                [sys.executable, script_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env={
                    **os.environ,
                    "SONIQ_DATABASE_URL": TEST_DATABASE_URL,
                    "PYTHONPATH": str(PROJECT_ROOT),
                },
            )

            await asyncio.sleep(2)

            process.send_signal(signal.SIGINT)

            try:
                stdout, stderr = process.communicate(timeout=10)

                assert process.returncode in [
                    0,
                    -signal.SIGINT,
                    130,
                ], f"Exit code {process.returncode}, stdout={stdout!r}, stderr={stderr!r}"

                output = stdout + stderr
                shutdown_keywords = [
                    "shutdown",
                    "stopped",
                    "closing",
                    "cleanup",
                    "graceful",
                ]
                has_shutdown_message = any(
                    keyword in output.lower() for keyword in shutdown_keywords
                )

                assert has_shutdown_message or process.returncode == 0, (
                    f"Expected graceful shutdown message OR clean exit, got returncode {process.returncode} "
                    f"with output: {output}"
                )

            except subprocess.TimeoutExpired:
                process.kill()
                pytest.fail("Worker did not shut down gracefully within timeout")

        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            if os.path.exists(script_path):
                os.unlink(script_path)

    @pytest.mark.asyncio
    async def test_sigterm_graceful_shutdown(self):
        """Test SIGTERM graceful shutdown for instance API worker"""
        script_path = _write_worker_script()

        try:
            process = subprocess.Popen(
                [sys.executable, script_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env={
                    **os.environ,
                    "SONIQ_DATABASE_URL": TEST_DATABASE_URL,
                    "PYTHONPATH": str(PROJECT_ROOT),
                },
            )

            await asyncio.sleep(2)

            process.send_signal(signal.SIGTERM)

            try:
                stdout, stderr = process.communicate(timeout=10)

                assert process.returncode in [
                    0,
                    -signal.SIGTERM,
                    143,
                ], f"Exit code {process.returncode}, stdout={stdout!r}, stderr={stderr!r}"

                output = stdout + stderr
                shutdown_keywords = [
                    "shutdown",
                    "stopped",
                    "closing",
                    "cleanup",
                    "graceful",
                ]
                filtered_lines = [
                    line
                    for line in output.strip().splitlines()
                    if "SONIQ_SKIP_UPDATE_LOCK" not in line
                ]
                filtered_output = "\n".join(filtered_lines).strip()

                has_shutdown_message = any(
                    keyword in output.lower() for keyword in shutdown_keywords
                )
                silent_shutdown = filtered_output == ""
                assert (
                    has_shutdown_message or silent_shutdown
                ), f"Expected shutdown keywords or silent shutdown, got: {repr(output)}"

            except subprocess.TimeoutExpired:
                process.kill()
                pytest.fail("Worker did not shut down gracefully within timeout")

        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            if os.path.exists(script_path):
                os.unlink(script_path)

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        not hasattr(signal, "SIGHUP"), reason="SIGHUP not available on this platform"
    )
    async def test_sighup_graceful_shutdown(self):
        """Test SIGHUP graceful shutdown for instance API worker"""
        script_path = _write_worker_script()

        try:
            process = subprocess.Popen(
                [sys.executable, script_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env={
                    **os.environ,
                    "SONIQ_DATABASE_URL": TEST_DATABASE_URL,
                    "PYTHONPATH": str(PROJECT_ROOT),
                },
            )

            await asyncio.sleep(2)

            process.send_signal(signal.SIGHUP)

            try:
                stdout, stderr = process.communicate(timeout=10)

                assert process.returncode in [
                    0,
                    -signal.SIGHUP,
                    129,
                ], f"Exit code {process.returncode}, stdout={stdout!r}, stderr={stderr!r}"

                output = stdout + stderr
                shutdown_keywords = [
                    "shutdown",
                    "stopped",
                    "closing",
                    "cleanup",
                    "graceful",
                ]
                has_shutdown_message = any(
                    keyword in output.lower() for keyword in shutdown_keywords
                )

                assert has_shutdown_message or process.returncode == 0, (
                    f"Expected graceful shutdown message OR clean exit, got returncode {process.returncode} "
                    f"with output: {output}"
                )

            except subprocess.TimeoutExpired:
                process.kill()
                pytest.fail("Worker did not shut down gracefully within timeout")

        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            if os.path.exists(script_path):
                os.unlink(script_path)


class TestCLISignalHandling:
    """Test signal handling through the CLI interface"""

    @pytest.mark.asyncio
    async def test_cli_worker_sigterm_shutdown(self):
        """Test that `soniq worker` handles SIGTERM gracefully"""

        process = subprocess.Popen(
            [sys.executable, "-m", "soniq.cli.main", "worker", "--concurrency", "1"],
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={
                **os.environ,
                "SONIQ_DATABASE_URL": TEST_DATABASE_URL,
                "PYTHONPATH": str(PROJECT_ROOT),
            },
        )

        try:
            await asyncio.sleep(2)

            process.send_signal(signal.SIGTERM)

            try:
                stdout, stderr = process.communicate(timeout=10)

                assert process.returncode in [
                    0,
                    -signal.SIGTERM,
                    143,
                ], f"Exit code {process.returncode}, stdout={stdout!r}, stderr={stderr!r}"

                output = stdout + stderr
                shutdown_keywords = [
                    "shutdown",
                    "stopped",
                    "graceful",
                    "closing",
                    "cleanup",
                ]
                has_shutdown_message = any(
                    keyword in output.lower() for keyword in shutdown_keywords
                )
                assert has_shutdown_message or process.returncode == 0, (
                    f"Expected shutdown keywords OR clean exit, got returncode {process.returncode} "
                    f"with output: {output}"
                )

            except subprocess.TimeoutExpired:
                process.kill()
                pytest.fail("CLI worker did not shut down gracefully within timeout")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()

    @pytest.mark.asyncio
    async def test_cli_worker_sigint_shutdown(self):
        """Test that `soniq worker` handles SIGINT (Ctrl+C) gracefully"""

        process = subprocess.Popen(
            [sys.executable, "-m", "soniq.cli.main", "worker", "--concurrency", "1"],
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={
                **os.environ,
                "SONIQ_DATABASE_URL": TEST_DATABASE_URL,
                "PYTHONPATH": str(PROJECT_ROOT),
            },
        )

        try:
            await asyncio.sleep(2)

            process.send_signal(signal.SIGINT)

            try:
                stdout, stderr = process.communicate(timeout=10)

                assert process.returncode in [
                    0,
                    -signal.SIGINT,
                    130,
                ], f"Exit code {process.returncode}, stdout={stdout!r}, stderr={stderr!r}"

                output = stdout + stderr
                shutdown_keywords = [
                    "shutdown",
                    "stopped",
                    "interrupt",
                    "closing",
                    "cleanup",
                    "graceful",
                ]
                has_shutdown_message = any(
                    keyword in output.lower() for keyword in shutdown_keywords
                )
                assert has_shutdown_message or process.returncode == 0, (
                    f"Expected shutdown keywords OR clean exit, got returncode {process.returncode} "
                    f"with output: {output}"
                )

            except subprocess.TimeoutExpired:
                process.kill()
                pytest.fail("CLI worker did not shut down gracefully within timeout")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()


@pytest.mark.asyncio
async def test_signal_handler_cross_platform_compatibility():
    """Test that signal handlers work across different platforms"""
    from soniq.utils.signals import GracefulSignalHandler

    handler = GracefulSignalHandler()
    shutdown_event = asyncio.Event()

    try:
        handler.setup_signal_handlers(shutdown_event)

        assert signal.SIGINT in handler.handled_signals

        assert len(handler.handled_signals) >= 1

    finally:
        handler.restore_signal_handlers()


@pytest.mark.asyncio
async def test_signal_handler_multiple_setup_cleanup():
    """Test that multiple setup/cleanup cycles work correctly"""
    from soniq.utils.signals import GracefulSignalHandler

    handler = GracefulSignalHandler()

    for i in range(3):
        shutdown_event = asyncio.Event()

        handler.setup_signal_handlers(shutdown_event)
        assert len(handler.handled_signals) > 0

        handler.restore_signal_handlers()
        assert len(handler.handled_signals) == 0
        assert handler.shutdown_event is None
