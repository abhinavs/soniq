"""
Root conftest — minimal, no database required.

Each test tier has its own conftest:
- tests/unit/conftest.py — MemoryBackend
- tests/functional/conftest.py — SQLiteBackend
- tests/integration/conftest.py — PostgreSQL
- tests/smoke/ — no conftest needed
"""

import asyncio

import pytest


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for each test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(autouse=True)
def _dashboard_test_host():
    # TestClient sends "Host: testserver", which a keyless dashboard rejects.
    # Its own MonkeyPatch, so tests' `monkeypatch` still undoes first.
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("SONIQ_DASHBOARD_ALLOWED_HOSTS", "testserver,test,x")
        yield
