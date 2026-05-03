import asyncio
import os

import pytest

from soniq import Soniq
from tests.db_utils import TEST_DATABASE_URL, clear_table, create_test_database

os.environ.setdefault("SONIQ_DATABASE_URL", TEST_DATABASE_URL)
os.environ.setdefault("SONIQ_JOBS_MODULES", "tests.fixtures.cli_jobs")

_TEST_DATABASE_URL = os.environ["SONIQ_DATABASE_URL"]


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for each test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session", autouse=True)
async def setup_test_database():
    """Set up test database once for the entire test session."""
    await create_test_database()
    yield


@pytest.fixture
async def soniq_app():
    """Yield a fresh Soniq instance with a clean database state."""
    app = Soniq(database_url=_TEST_DATABASE_URL)
    pool = await app._get_pool()
    await clear_table(pool)
    yield app
    if app.is_initialized and not app.is_closed:
        await app.close()


@pytest.fixture(autouse=True)
async def _clear_tables_before_each_test():
    """Clear database tables before every test for isolation."""
    app = Soniq(database_url=_TEST_DATABASE_URL)
    pool = await app._get_pool()
    await clear_table(pool)
    await app.close()
