"""
Unit test conftest - MemoryBackend, zero external dependencies.

No PostgreSQL, no SQLite files, no network. Pure in-memory.
"""

import pytest


@pytest.fixture(autouse=True)
async def reset_global_state():
    """Reset settings cache between unit tests."""
    yield

    from soniq.settings import get_settings

    get_settings(reload=True)
