"""
Conftest for Instance API tests

These tests use the instance-based Soniq API (app = Soniq(), app.job, etc.)
and create their own isolated Soniq instances.
"""

import os

import pytest

from tests.db_utils import TEST_DATABASE_URL, clear_table, create_test_database

os.environ["SONIQ_DATABASE_URL"] = TEST_DATABASE_URL


@pytest.fixture(scope="session", autouse=True)
async def setup_instance_test_database():
    """Set up test database once per test session."""
    await create_test_database()
    yield


@pytest.fixture
async def clean_db():
    """Additional fixture for tests that need explicit clean database state - FAST VERSION."""
    from soniq.app import Soniq

    app = Soniq(database_url=TEST_DATABASE_URL)
    pool = await app._get_pool()
    await clear_table(pool)
    await app.close()
    return None
