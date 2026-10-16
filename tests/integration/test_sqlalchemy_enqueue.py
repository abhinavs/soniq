"""Transactional enqueue with an SQLAlchemy AsyncSession / AsyncConnection, and with
a plain asyncpg connection that has no Soniq JSONB codec."""

import asyncpg
import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import Integer, String, text  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column  # noqa: E402

from soniq import Soniq  # noqa: E402
from tests.db_utils import TEST_DATABASE_URL  # noqa: E402

SA_URL = TEST_DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)


class Base(DeclarativeBase):
    pass


class Order(Base):
    __tablename__ = "sa_test_orders"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    note: Mapped[str] = mapped_column(String(50))


@pytest.fixture
async def engine():
    eng = create_async_engine(SA_URL)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await eng.dispose()


@pytest.fixture
async def soniq():
    app = Soniq(database_url=TEST_DATABASE_URL)

    @app.job(name="sa_send_invoice")
    async def sa_send_invoice(order_id: int):
        return None

    @app.job(name="sa_with_session")
    async def sa_with_session(session: str):
        return None

    await app.ensure_initialized()
    app.test_jobs = {"send_invoice": sa_send_invoice}  # type: ignore[attr-defined]
    yield app
    await app.close()


async def _job_args(app: Soniq, job_id: str):
    job = await app.get_job(job_id)
    return None if job is None else job["args"]


async def test_session_commit_makes_job_visible_with_flushed_id(engine, soniq):
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        async with db.begin():
            order = Order(note="x")
            db.add(order)
            await db.flush()
            send_invoice = soniq.test_jobs["send_invoice"]
            job_id = await soniq.enqueue(send_invoice, connection=db, order_id=order.id)
    assert order.id is not None
    assert await _job_args(soniq, job_id) == {"order_id": order.id}


async def test_session_rollback_removes_job(engine, soniq):
    Session = async_sessionmaker(engine)
    async with Session() as db:
        with pytest.raises(RuntimeError):
            async with db.begin():
                db.add(Order(note="x"))
                await db.flush()
                job_id = await soniq.enqueue(
                    "sa_send_invoice", connection=db, args={"order_id": 1}
                )
                raise RuntimeError("rollback")
    assert await _job_args(soniq, job_id) is None


async def test_rollback_when_enqueue_is_the_first_statement(engine, soniq):
    # SQLAlchemy's asyncpg adapter sends BEGIN lazily; the job must still roll back.
    async with engine.connect() as conn:
        with pytest.raises(RuntimeError):
            async with conn.begin():
                job_id = await soniq.enqueue(
                    "sa_send_invoice", connection=conn, args={"order_id": 1}
                )
                raise RuntimeError("rollback")
    assert await _job_args(soniq, job_id) is None


async def test_async_connection_commit(engine, soniq):
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO sa_test_orders (note) VALUES ('y')"))
        job_id = await soniq.enqueue(
            "sa_send_invoice", connection=conn, args={"order_id": 2}
        )
    assert await _job_args(soniq, job_id) == {"order_id": 2}


async def test_session_without_transaction_gives_clear_error(engine, soniq):
    from soniq.errors import SoniqError

    async with AsyncSession(engine) as db:
        with pytest.raises(SoniqError, match="no active transaction"):
            await soniq.enqueue("sa_send_invoice", connection=db, args={"order_id": 1})


async def test_job_with_session_parameter_still_receives_it(engine, soniq):
    job_id = await soniq.enqueue("sa_with_session", args={"session": "abc"})
    assert await _job_args(soniq, job_id) == {"session": "abc"}

    async def sa_with_session(session: str):
        return None

    sa_with_session._soniq_name = "sa_with_session"  # type: ignore[attr-defined]
    Session = async_sessionmaker(engine)
    async with Session() as db:
        async with db.begin():
            job_id = await soniq.enqueue(
                sa_with_session, connection=db, session="from-kwarg"
            )
    assert await _job_args(soniq, job_id) == {"session": "from-kwarg"}


async def test_raw_asyncpg_connection_is_passed_through_unchanged(soniq, monkeypatch):
    seen = []
    original = soniq.backend.create_job_transactional

    async def spy(**kwargs):
        seen.append(kwargs["connection"])
        return await original(**kwargs)

    monkeypatch.setattr(soniq.backend, "create_job_transactional", spy)
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        async with conn.transaction():
            await soniq.enqueue(
                "sa_send_invoice", connection=conn, args={"order_id": 3}
            )
    finally:
        await conn.close()
    assert len(seen) == 1 and seen[0] is conn


async def test_plain_asyncpg_connection_without_codec(soniq):
    # A connection from asyncpg.connect() has no Soniq JSONB codec registered.
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        async with conn.transaction():
            job_id = await soniq.enqueue(
                "sa_send_invoice", connection=conn, args={"order_id": 4}
            )
        unique_id = await soniq.enqueue(
            "sa_send_invoice", connection=conn, args={"order_id": 5}, unique=True
        )
        dedup_id = await soniq.enqueue(
            "sa_send_invoice", connection=conn, args={"order_id": 6}, dedup_key="k6"
        )
    finally:
        await conn.close()
    assert await _job_args(soniq, job_id) == {"order_id": 4}
    assert await _job_args(soniq, unique_id) == {"order_id": 5}
    assert await _job_args(soniq, dedup_id) == {"order_id": 6}
