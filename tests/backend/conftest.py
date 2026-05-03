"""
Backend conformance test fixtures.

Parametrized over Memory and SQLite backends.
Postgres conformance runs separately in tests/integration/.
"""

import pytest

from soniq.testing.memory_backend import MemoryBackend


def _get_backend_params():
    params = ["memory"]
    try:
        import aiosqlite  # noqa: F401

        params.append("sqlite")
    except ImportError:
        pass
    return params


@pytest.fixture(params=_get_backend_params())
async def backend(request, tmp_path):
    if request.param == "memory":
        b = MemoryBackend()
    elif request.param == "sqlite":
        from soniq.backends.sqlite import SQLiteBackend

        b = SQLiteBackend(str(tmp_path / "test.db"))
    await b.initialize()
    yield b
    await b.close()
