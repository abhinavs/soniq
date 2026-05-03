"""
`_ensure_postgres_database_exists` interpolates the db name into a CREATE
DATABASE statement. The name is operator-controlled (comes from the
connection URL) so this is not a classic injection vector, but a stray
quote, space, or backslash in the name still breaks quoting. Validating
the identifier up front converts "mysteriously broken" into "clear error".
"""

import pytest

from soniq import Soniq


@pytest.mark.asyncio
async def test_rejects_db_name_with_quote():
    app = Soniq(database_url='postgresql://u@h/bad"name')
    with pytest.raises(ValueError, match="non-identifier name"):
        await app._ensure_postgres_database_exists()


@pytest.mark.asyncio
async def test_rejects_db_name_with_space():
    app = Soniq(database_url="postgresql://u@h/bad name")
    with pytest.raises(ValueError, match="non-identifier name"):
        await app._ensure_postgres_database_exists()


@pytest.mark.asyncio
async def test_rejects_db_name_with_semicolon():
    app = Soniq(database_url="postgresql://u@h/bad;name")
    with pytest.raises(ValueError, match="non-identifier name"):
        await app._ensure_postgres_database_exists()


@pytest.mark.asyncio
async def test_accepts_typical_db_name(monkeypatch):
    """Regression guard: a conventional identifier does not raise here.

    We stub asyncpg so the test does not actually hit a database; the point
    is that validation passes and the connect path is attempted.
    """
    called = {}

    class _FakeConn:
        async def fetchval(self, *_a, **_k):
            return 1  # pretend db exists

        async def close(self):
            called["closed"] = True

    async def _fake_connect(url):
        called["url"] = url
        return _FakeConn()

    import asyncpg

    monkeypatch.setattr(asyncpg, "connect", _fake_connect)

    app = Soniq(database_url="postgresql://u@h/my_app_123")
    await app._ensure_postgres_database_exists()
    assert called.get("closed") is True
