"""Unit tests for soniq.core.leadership."""

import pytest

from soniq.core.leadership import advisory_key, with_advisory_lock


def test_advisory_key_is_deterministic():
    assert advisory_key("foo") == advisory_key("foo")


def test_advisory_key_differs_across_names():
    assert advisory_key("foo") != advisory_key("bar")


def test_advisory_key_is_64_bit_signed():
    k = advisory_key("soniq.recurring_scheduler")
    assert -(2**63) <= k < 2**63


def test_advisory_key_stable_across_processes(tmp_path):
    """
    Verify the key is stable across Python processes.

    Python's built-in hash() is salted per-process via PYTHONHASHSEED, so a
    naive hash-based key would differ across workers. blake2b is not salted.
    """
    import subprocess
    import sys

    script = tmp_path / "key_probe.py"
    script.write_text(
        "from soniq.core.leadership import advisory_key\n"
        "print(advisory_key('soniq.recurring_scheduler'))\n"
    )
    first = subprocess.check_output([sys.executable, str(script)]).decode().strip()
    second = subprocess.check_output([sys.executable, str(script)]).decode().strip()
    assert first == second
    assert int(first) == advisory_key("soniq.recurring_scheduler")


@pytest.mark.asyncio
async def test_with_advisory_lock_no_backend_support_yields_true():
    """Backends without with_advisory_lock (e.g. Memory) always yield True."""

    class FakeBackend:
        pass

    async with with_advisory_lock(FakeBackend(), "any.name") as leader:
        assert leader is True


@pytest.mark.asyncio
async def test_with_advisory_lock_delegates_to_backend():
    """Backends that implement with_advisory_lock are delegated to."""
    calls = []

    class TrackingBackend:
        def with_advisory_lock(self, name):
            calls.append(name)

            class _CM:
                async def __aenter__(self_inner):
                    return False

                async def __aexit__(self_inner, *a):
                    return None

            return _CM()

    async with with_advisory_lock(TrackingBackend(), "soniq.tests") as leader:
        assert leader is False
    assert calls == ["soniq.tests"]
