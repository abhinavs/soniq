"""
Conftest for Infrastructure tests

These tests cover underlying systems (CLI, connections, LISTEN/NOTIFY, etc.)
that support both APIs and may use mixed patterns.
"""

import os

from tests.db_utils import TEST_DATABASE_URL

# Ensure test database URL is set
os.environ["SONIQ_DATABASE_URL"] = TEST_DATABASE_URL
