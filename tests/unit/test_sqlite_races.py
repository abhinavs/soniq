"""
Concurrent coroutines share the SQLite backend's single connection. Its
check-then-write steps must not interleave.
"""

import asyncio

import pytest

pytest.importorskip("aiosqlite")

from soniq import Soniq  # noqa: E402


@pytest.mark.asyncio
async def test_concurrent_unique_and_dedup_enqueues_insert_one_row(tmp_path):
    app = Soniq(database_url=f"sqlite:///{tmp_path}/t.db")

    @app.job(name="race.u", unique=True)
    async def u(a: int): ...

    await app.ensure_initialized()
    ids = await asyncio.gather(*[app.enqueue(u, a=1) for _ in range(30)])
    assert len(set(ids)) == 1

    ids = await asyncio.gather(
        *[app.enqueue(u, a=i + 100, dedup_key="dk") for i in range(30)]
    )
    assert len(set(ids)) == 1
    await app.close()


@pytest.mark.asyncio
async def test_concurrent_claims_never_hand_out_the_same_job(tmp_path):
    app = Soniq(database_url=f"sqlite:///{tmp_path}/t.db")

    @app.job(name="race.j")
    async def j(n: int): ...

    await app.ensure_initialized()
    for n in range(10):
        await app.enqueue(j, n=n)

    claims = await asyncio.gather(
        *[app.backend.fetch_and_lock_job(worker_id=None) for _ in range(20)]
    )
    claimed = [c["id"] for c in claims if c]
    assert len(claimed) == 10
    assert len(set(claimed)) == 10
    await app.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_empty_dedup_key_still_dedupes(backend, tmp_path):
    if backend == "memory":
        app = Soniq(backend="memory")
    else:
        app = Soniq(database_url=f"sqlite:///{tmp_path}/t.db")

    @app.job(name="race.dk")
    async def dk(a: int): ...

    first = await app.enqueue(dk, a=1, dedup_key="")
    second = await app.enqueue(dk, a=2, dedup_key="")
    assert first == second
    await app.close()
