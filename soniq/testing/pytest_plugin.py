"""Opt-in pytest plugin for Soniq.

Enable it in `conftest.py`:

    pytest_plugins = ["soniq.testing.pytest_plugin"]

or on the command line: `pytest -p soniq.testing.pytest_plugin`.

Fixtures:

- `soniq_memory`: a `Soniq` on the in-memory backend.
- `soniq_pg`: a `Soniq` on PostgreSQL (`SONIQ_DATABASE_URL`). All work
  in the test runs in one transaction that is rolled back at the end.

Both fixtures read the `soniq` marker. `@pytest.mark.soniq(app="pkg.mod:app")`
copies the jobs, hooks and middleware of your own instance into the fixture.
"""

import importlib
import os
from contextlib import asynccontextmanager
from typing import Any

import pytest
import pytest_asyncio

from soniq import Soniq


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", 'soniq(app="module:attr"): use the jobs of this Soniq instance'
    )


def _build(request: pytest.FixtureRequest, **kwargs: Any) -> Soniq:
    app = Soniq(**kwargs)
    marker = request.node.get_closest_marker("soniq")
    path = marker.kwargs.get("app") if marker else None
    if path:
        module, _, attr = path.partition(":")
        source = getattr(importlib.import_module(module), attr)
        app._job_registry = source._job_registry
        app._hooks = source._hooks
        app._middleware = source._middleware
    return app


@pytest.fixture
def soniq_memory(request: pytest.FixtureRequest) -> Soniq:
    return _build(request, backend="memory")


class _OneConnectionPool:
    """Pool stand-in that always lends the same connection."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def acquire(self) -> Any:
        @asynccontextmanager
        async def lend() -> Any:
            yield self._conn

        return lend()

    async def close(self) -> None:
        pass


@pytest_asyncio.fixture
async def soniq_pg(request: pytest.FixtureRequest) -> Any:
    url = os.environ.get("SONIQ_DATABASE_URL")
    if not url:
        pytest.skip("soniq_pg needs SONIQ_DATABASE_URL")
    import asyncpg

    from soniq.backends.postgres import PostgresBackend, _init_connection

    # Create the schema first. It is committed and stays between tests.
    setup_app = _build(request, database_url=url)
    await setup_app.setup()
    await setup_app.close()
    conn = await asyncpg.connect(url)
    await _init_connection(conn)
    tx = conn.transaction()
    await tx.start()
    backend = PostgresBackend(database_url=url)
    backend._pool = _OneConnectionPool(conn)  # type: ignore[assignment]
    app = _build(request, backend=backend)
    try:
        yield app
    finally:
        await tx.rollback()
        await conn.close()
