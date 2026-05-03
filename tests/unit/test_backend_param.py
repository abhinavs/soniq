"""
Tests that Soniq accepts a backend parameter.
"""

import inspect

import pytest


def test_soniq_init_accepts_backend():
    """Soniq.__init__ should accept a backend parameter."""
    from soniq.app import Soniq

    sig = inspect.signature(Soniq.__init__)
    assert "backend" in sig.parameters


def test_soniq_exposes_backend_attribute():
    """Soniq should expose the _backend attribute for internal use."""
    from soniq.app import Soniq

    app = Soniq(backend="memory")
    assert hasattr(app, "_backend")


def test_soniq_resolves_memory_backend_string():
    """Soniq(backend='memory') should create a MemoryBackend."""
    from soniq.app import Soniq
    from soniq.testing.memory_backend import MemoryBackend

    app = Soniq(backend="memory")
    assert isinstance(app._backend, MemoryBackend)


def test_soniq_resolves_sqlite_backend_string(tmp_path):
    """Soniq(backend='sqlite') should create a SQLiteBackend."""
    pytest.importorskip("aiosqlite")
    from soniq.app import Soniq
    from soniq.backends.sqlite import SQLiteBackend

    app = Soniq(backend="sqlite", database_url=str(tmp_path / "test.db"))
    assert isinstance(app._backend, SQLiteBackend)


def test_soniq_unknown_backend_raises():
    """Soniq(backend='redis') should raise ValueError."""
    import pytest

    from soniq.app import Soniq

    with pytest.raises(ValueError, match="Unknown backend"):
        Soniq(backend="redis")
