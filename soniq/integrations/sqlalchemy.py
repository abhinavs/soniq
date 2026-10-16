"""Use an SQLAlchemy async session or connection as ``enqueue(connection=...)``.

SQLAlchemy is imported lazily, so this module costs nothing when it is not used.
"""

from typing import Any

import asyncpg

from soniq.errors import SoniqError

SONIQ_UNSUPPORTED_CONNECTION = "SONIQ_UNSUPPORTED_CONNECTION"


async def to_asyncpg_connection(obj: Any) -> asyncpg.Connection:
    """Return the asyncpg connection under an AsyncSession or AsyncConnection.

    The session or connection must be inside a transaction and use the asyncpg driver.
    """
    from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

    if isinstance(obj, AsyncSession):
        if not obj.in_transaction():
            raise _no_transaction(obj)
        conn = await obj.connection()
    elif isinstance(obj, AsyncConnection):
        if not obj.in_transaction():
            raise _no_transaction(obj)
        conn = obj
    else:
        raise SoniqError(
            f"enqueue(connection=...) accepts an SQLAlchemy AsyncSession or "
            f"AsyncConnection, got {type(obj).__name__}.",
            SONIQ_UNSUPPORTED_CONNECTION,
            context={"type": f"{type(obj).__module__}.{type(obj).__name__}"},
        )

    driver = conn.dialect.driver
    if driver != "asyncpg":
        raise SoniqError(
            f"Transactional enqueue with SQLAlchemy needs the asyncpg driver, "
            f"but this engine uses {driver!r}. Support for psycopg is planned.",
            SONIQ_UNSUPPORTED_CONNECTION,
            context={"driver": driver},
            suggestions=["Create the engine with a 'postgresql+asyncpg://' URL."],
        )

    raw = await conn.get_raw_connection()
    driver_conn: asyncpg.Connection = raw.driver_connection
    # SQLAlchemy's asyncpg adapter sends BEGIN lazily, on the first statement.
    # If nothing ran yet, run one so the job insert joins the transaction.
    if not driver_conn.is_in_transaction():
        await conn.exec_driver_sql("SELECT 1")
    return driver_conn


def _no_transaction(obj: Any) -> SoniqError:
    return SoniqError(
        f"The SQLAlchemy {type(obj).__name__} passed as enqueue(connection=...) "
        f"has no active transaction, so the job would not be atomic with your writes.",
        SONIQ_UNSUPPORTED_CONNECTION,
        suggestions=[
            "Call enqueue() inside 'async with session.begin():' "
            "or 'async with conn.begin():'."
        ],
    )
