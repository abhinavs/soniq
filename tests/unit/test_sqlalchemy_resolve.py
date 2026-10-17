"""Error paths of the SQLAlchemy connection resolver. No database needed."""

from unittest.mock import MagicMock

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy.ext.asyncio import AsyncConnection  # noqa: E402

from soniq.errors import SoniqError  # noqa: E402
from soniq.integrations.sqlalchemy import to_asyncpg_connection  # noqa: E402


def _fake_conn(*, in_txn: bool = True, driver: str = "asyncpg") -> MagicMock:
    conn = MagicMock(spec=AsyncConnection)
    conn.in_transaction.return_value = in_txn
    conn.dialect.driver = driver
    return conn


async def test_wrong_driver_gives_clear_error():
    with pytest.raises(SoniqError) as exc:
        await to_asyncpg_connection(_fake_conn(driver="aiosqlite"))
    assert "asyncpg driver" in str(exc.value)
    assert "aiosqlite" in str(exc.value)


async def test_no_transaction_gives_clear_error():
    with pytest.raises(SoniqError) as exc:
        await to_asyncpg_connection(_fake_conn(in_txn=False))
    assert "no active transaction" in str(exc.value)


async def test_other_sqlalchemy_object_is_rejected():
    with pytest.raises(SoniqError) as exc:
        await to_asyncpg_connection(object())
    assert "AsyncSession" in str(exc.value)
