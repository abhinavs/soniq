"""
Tests for the soniq_task_registry observability table.

LOAD-BEARING INVARIANT: this table is observability metadata ONLY.
The enqueue path never reads it. Tests here exercise the backend
methods (register_task_name, list_registered_task_names) plus the
boundary that Soniq.enqueue does not consult them.
"""

from __future__ import annotations

import os

import pytest

from tests.db_utils import TEST_DATABASE_URL

os.environ.setdefault("SONIQ_DATABASE_URL", TEST_DATABASE_URL)

from soniq.testing.memory_backend import MemoryBackend  # noqa: E402

# ---------------------------------------------------------------------------
# MemoryBackend coverage of the registry methods
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_task_name_inserts_row():
    backend = MemoryBackend()
    await backend.register_task_name(task_name="billing.foo", worker_id="w1")
    rows = await backend.list_registered_task_names()
    assert len(rows) == 1
    row = rows[0]
    assert row["task_name"] == "billing.foo"
    assert row["worker_id"] == "w1"
    assert row["last_seen_at"] is not None


@pytest.mark.asyncio
async def test_register_task_name_upsert_on_repeat():
    backend = MemoryBackend()
    await backend.register_task_name(task_name="billing.foo", worker_id="w1")
    rows1 = await backend.list_registered_task_names()
    first_seen = rows1[0]["last_seen_at"]

    # Re-registering the same (name, worker_id) updates last_seen_at.
    await backend.register_task_name(task_name="billing.foo", worker_id="w1")
    rows2 = await backend.list_registered_task_names()
    assert len(rows2) == 1  # still one row
    assert rows2[0]["last_seen_at"] >= first_seen


@pytest.mark.asyncio
async def test_multiple_workers_same_task_name():
    """Composite (task_name, worker_id) PK gives per-worker visibility:
    two workers handling the same name produce two rows."""
    backend = MemoryBackend()
    await backend.register_task_name(task_name="billing.foo", worker_id="w1")
    await backend.register_task_name(task_name="billing.foo", worker_id="w2")
    rows = await backend.list_registered_task_names()
    assert len(rows) == 2
    workers = {r["worker_id"] for r in rows}
    assert workers == {"w1", "w2"}


@pytest.mark.asyncio
async def test_args_model_repr_persisted():
    backend = MemoryBackend()
    await backend.register_task_name(
        task_name="billing.bar",
        worker_id="w1",
        args_model_repr="<class 'BarArgs'>",
    )
    rows = await backend.list_registered_task_names()
    assert rows[0]["args_model_repr"] == "<class 'BarArgs'>"


@pytest.mark.asyncio
async def test_list_returns_empty_when_nothing_registered():
    backend = MemoryBackend()
    assert await backend.list_registered_task_names() == []


# ---------------------------------------------------------------------------
# Architectural boundary: Soniq.enqueue does not consult this table
# ---------------------------------------------------------------------------


def test_app_module_does_not_reference_registry_table_methods():
    """Structural guard: soniq/app.py must not call
    list_registered_task_names or register_task_name. The registry
    table is observability metadata only."""
    import inspect

    import soniq.app as app_mod

    src = inspect.getsource(app_mod)
    assert "list_registered_task_names" not in src
    assert "register_task_name" not in src


@pytest.mark.asyncio
async def test_strict_enqueue_does_not_consult_registry_table():
    """Even when the registry table is fully populated for a name, strict
    enqueue still raises SONIQ_UNKNOWN_TASK_NAME if the in-process
    registry is empty. This is the load-bearing 'observability only'
    invariant."""
    from soniq import Soniq
    from soniq.errors import SONIQ_UNKNOWN_TASK_NAME, SoniqError

    backend = MemoryBackend()
    # Populate the registry table - simulating workers across the fleet.
    await backend.register_task_name(task_name="billing.populated", worker_id="w1")
    await backend.register_task_name(task_name="billing.populated", worker_id="w2")

    app = Soniq(backend=backend, enqueue_validation="strict")
    with pytest.raises(SoniqError) as exc_info:
        await app.enqueue("billing.populated", args={})
    assert exc_info.value.error_code == SONIQ_UNKNOWN_TASK_NAME

    # And no row got written to soniq_jobs.
    rows = await app.list_jobs()
    assert not any(r["job_name"] == "billing.populated" for r in rows)


# ---------------------------------------------------------------------------
# Worker-side population
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_populates_task_registry_on_startup():
    """When a worker starts, it upserts the names it handles into the
    observability table so the dashboard / tasks check CLI can see them."""
    from soniq import Soniq

    backend = MemoryBackend()
    app = Soniq(backend=backend, enqueue_validation="none")

    @app.job(name="billing.handler.a")
    async def handler_a():
        pass

    @app.job(name="billing.handler.b")
    async def handler_b():
        pass

    # Run one tick of the worker (run_once exercises the same startup
    # path that register_task_name runs from).
    await app.run_worker(run_once=True)

    rows = await backend.list_registered_task_names()
    names = {r["task_name"] for r in rows}
    assert "billing.handler.a" in names
    assert "billing.handler.b" in names
