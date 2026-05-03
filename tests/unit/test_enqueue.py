"""
Tests for Soniq.enqueue (name_or_ref, *, args=dict, ...).

These tests run against MemoryBackend so they have no Postgres dependency.
"""

import os

import pytest

from tests.db_utils import TEST_DATABASE_URL

os.environ.setdefault("SONIQ_DATABASE_URL", TEST_DATABASE_URL)

from soniq.errors import (  # noqa: E402
    SONIQ_INVALID_TASK_NAME,
    SONIQ_TASK_ARGS_INVALID,
    SONIQ_UNKNOWN_TASK_NAME,
    SoniqError,
)
from soniq.testing import make_app  # noqa: E402


@pytest.fixture
def app():
    """A fresh in-memory Soniq with strict validation (the production default)."""
    return make_app(enqueue_validation="strict")


@pytest.fixture
def lenient_app():
    """In-memory Soniq with enqueue_validation='none' for pure-producer tests."""
    return make_app(enqueue_validation="none")


# ---------------------------------------------------------------------------
# Basic shape: registered task, default mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enqueue_writes_row_for_registered_task(app):
    @app.job(name="billing.foo")
    async def foo(order_id: str):
        pass

    job_id = await app.enqueue("billing.foo", args={"order_id": "o1"})
    assert isinstance(job_id, str) and len(job_id) > 0

    rows = await app.list_jobs()
    assert any(r["job_name"] == "billing.foo" for r in rows)


@pytest.mark.asyncio
async def test_enqueue_returns_uuid_string(app):
    @app.job(name="billing.bar")
    async def bar():
        pass

    job_id = await app.enqueue("billing.bar", args={})
    # UUID4 length is 36 characters with hyphens.
    assert len(job_id) == 36
    assert job_id.count("-") == 4


# ---------------------------------------------------------------------------
# Validation modes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_strict_mode_raises_for_unregistered_name_and_writes_nothing(app):
    with pytest.raises(SoniqError) as exc_info:
        await app.enqueue("billing.unknown", args={})
    assert exc_info.value.error_code == SONIQ_UNKNOWN_TASK_NAME

    rows = await app.list_jobs()
    # Crucially, no row was written.
    assert not any(r["job_name"] == "billing.unknown" for r in rows)


@pytest.mark.asyncio
async def test_none_mode_writes_row_for_unregistered_name(lenient_app):
    job_id = await lenient_app.enqueue("billing.unknown", args={"x": 1})
    rows = await lenient_app.list_jobs()
    assert any(r["id"] == job_id and r["job_name"] == "billing.unknown" for r in rows)


@pytest.mark.asyncio
async def test_warn_mode_logs_and_writes(caplog):
    import logging

    app = make_app(enqueue_validation="warn")
    with caplog.at_level(logging.WARNING, logger="soniq.app"):
        job_id = await app.enqueue("billing.unknown", args={})
    assert job_id
    rows = await app.list_jobs()
    assert any(r["job_name"] == "billing.unknown" for r in rows)
    assert any("billing.unknown" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Name-pattern validation (always on, regardless of mode)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_name", ["Bad Name", ".leading", "trailing.", "double..dot", "Has.Caps", ""]
)
async def test_invalid_name_format_raises_invalid_task_name(lenient_app, bad_name):
    with pytest.raises(SoniqError) as exc_info:
        await lenient_app.enqueue(bad_name, args={})
    assert exc_info.value.error_code == SONIQ_INVALID_TASK_NAME


@pytest.mark.asyncio
async def test_non_callable_non_string_non_ref_target_raises_typeerror(lenient_app):
    """target must be a callable, string, or TaskRef. Anything else (an int,
    a list, a random object) raises TypeError - not SoniqError, since the
    caller isn't even in the cross-service / by-name shape."""
    with pytest.raises(TypeError):
        await lenient_app.enqueue(123, args={})  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        await lenient_app.enqueue([1, 2, 3], args={})  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Callable target form (Celery-style)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enqueue_callable_with_kwargs():
    """`enqueue(callable, **kwargs)` - the Celery-equivalent shape. The
    name is read from `_soniq_name` (set by @app.job) or derived as
    f'{module}.{qualname}'; **kwargs are the function args."""
    app = make_app(enqueue_validation="strict")

    @app.job()
    async def send_welcome(user_id: int, template: str = "default"):
        pass

    job_id = await app.enqueue(send_welcome, user_id=42, template="signup")
    rows = await app.list_jobs()
    row = next(r for r in rows if r["id"] == job_id)
    assert row["job_name"] == send_welcome._soniq_name
    assert row["args"] == {"user_id": 42, "template": "signup"}


@pytest.mark.asyncio
async def test_enqueue_callable_explicit_name_used():
    """When @app.job(name=...) sets an explicit name, enqueue(callable)
    uses that name, not the derived one."""
    app = make_app(enqueue_validation="strict")

    @app.job(name="users.welcome")
    async def send_welcome(user_id: int):
        pass

    job_id = await app.enqueue(send_welcome, user_id=42)
    rows = await app.list_jobs()
    row = next(r for r in rows if r["id"] == job_id)
    assert row["job_name"] == "users.welcome"


@pytest.mark.asyncio
async def test_enqueue_callable_with_options():
    """Enqueue options (queue, priority) bind by name; remaining kwargs
    go into args."""
    app = make_app(enqueue_validation="none")

    @app.job()
    async def my_task(x: int, y: int):
        pass

    job_id = await app.enqueue(my_task, x=1, y=2, queue="urgent", priority=5)
    rows = await app.list_jobs()
    row = next(r for r in rows if r["id"] == job_id)
    assert row["args"] == {"x": 1, "y": 2}
    assert row["queue"] == "urgent"
    assert row["priority"] == 5


@pytest.mark.asyncio
async def test_enqueue_callable_rejects_args_dict():
    """Cannot mix args=dict with **func_kwargs in the callable form."""
    app = make_app(enqueue_validation="none")

    @app.job()
    async def my_task(x: int):
        pass

    with pytest.raises(TypeError):
        await app.enqueue(my_task, args={"x": 1}, x=2)


@pytest.mark.asyncio
async def test_enqueue_string_rejects_func_kwargs():
    """Cannot mix string target with **kwargs (they would collide with
    enqueue options like queue=/priority=)."""
    app = make_app(enqueue_validation="none")

    with pytest.raises(TypeError):
        await app.enqueue("some.task", x=1)  # type: ignore[call-arg]


@pytest.mark.asyncio
async def test_enqueue_taskref_rejects_func_kwargs():
    """Cannot mix TaskRef target with **kwargs; pass args=dict."""
    from soniq import task_ref

    app = make_app(enqueue_validation="none")
    ref = task_ref(name="billing.foo")

    with pytest.raises(TypeError):
        await app.enqueue(ref, x=1)  # type: ignore[call-arg]


@pytest.mark.asyncio
async def test_enqueue_callable_unregistered_derives_module_qualname():
    """A callable that wasn't registered via @app.job still has a name
    derived from f'{module}.{qualname}'. With enqueue_validation='none'
    it lands a row; with 'strict' it raises SONIQ_UNKNOWN_TASK_NAME."""
    lenient = make_app(enqueue_validation="none")

    async def naked_func(x: int):
        pass

    job_id = await lenient.enqueue(naked_func, x=1)
    rows = await lenient.list_jobs()
    row = next(r for r in rows if r["id"] == job_id)
    expected = f"{naked_func.__module__}.{naked_func.__name__}"
    assert row["job_name"] == expected


# ---------------------------------------------------------------------------
# args= is an explicit dict
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_args_defaults_to_empty_dict(app):
    @app.job(name="billing.empty")
    async def empty():
        pass

    job_id = await app.enqueue("billing.empty")
    rows = await app.list_jobs()
    row = next(r for r in rows if r["id"] == job_id)
    assert row["args"] == {}


@pytest.mark.asyncio
async def test_args_must_be_dict(app):
    @app.job(name="billing.foo")
    async def foo():
        pass

    with pytest.raises(TypeError):
        await app.enqueue("billing.foo", args=["x", "y"])  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Defaults from the registered job_meta
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_registered_defaults_applied_when_unset(app):
    @app.job(name="billing.priority", priority=20, queue="urgent", unique=True)
    async def priority_job(x: int):
        pass

    job_id = await app.enqueue("billing.priority", args={"x": 1})
    rows = await app.list_jobs()
    row = next(r for r in rows if r["id"] == job_id)
    assert row["priority"] == 20
    assert row["queue"] == "urgent"
    assert row["unique_job"] is True


@pytest.mark.asyncio
async def test_explicit_kwargs_override_registered_defaults(app):
    @app.job(name="billing.override", priority=20, queue="urgent")
    async def override_job():
        pass

    job_id = await app.enqueue(
        "billing.override", args={}, priority=5, queue="critical"
    )
    rows = await app.list_jobs()
    row = next(r for r in rows if r["id"] == job_id)
    assert row["priority"] == 5
    assert row["queue"] == "critical"


@pytest.mark.asyncio
async def test_unregistered_name_uses_system_defaults(lenient_app):
    """Pin the system defaults for the pure-producer (unregistered) path so a
    future refactor cannot silently change the on-the-wire shape."""
    job_id = await lenient_app.enqueue("billing.pure_producer", args={"x": 1})
    rows = await lenient_app.list_jobs()
    row = next(r for r in rows if r["id"] == job_id)
    assert row["priority"] == 100
    assert row["queue"] == "default"
    assert row["unique_job"] is False


# ---------------------------------------------------------------------------
# args_model validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_args_model_invalid_raises_task_args_invalid(app):
    from pydantic import BaseModel

    class InvoiceArgs(BaseModel):
        order_id: str
        customer: str

    @app.job(name="billing.invoice", validate=InvoiceArgs)
    async def invoice(order_id: str, customer: str):
        pass

    with pytest.raises(SoniqError) as exc_info:
        await app.enqueue(
            "billing.invoice", args={"order_id": "o1"}
        )  # missing customer
    assert exc_info.value.error_code == SONIQ_TASK_ARGS_INVALID

    rows = await app.list_jobs()
    assert not any(r["job_name"] == "billing.invoice" for r in rows)


@pytest.mark.asyncio
async def test_args_model_valid_passes(app):
    from pydantic import BaseModel

    class InvoiceArgs(BaseModel):
        order_id: str

    @app.job(name="billing.invoice2", validate=InvoiceArgs)
    async def invoice(order_id: str):
        pass

    job_id = await app.enqueue("billing.invoice2", args={"order_id": "o1"})
    assert job_id


# ---------------------------------------------------------------------------
# Transactional enqueue surface
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transactional_enqueue_unsupported_on_memory_backend(lenient_app):
    """MemoryBackend does not support transactional enqueue; assert the
    helpful error message from the wrapper."""
    with pytest.raises(ValueError, match="Transactional enqueue"):
        await lenient_app.enqueue(
            "billing.tx", args={}, connection=object()  # any non-None
        )


# ---------------------------------------------------------------------------
# Strict-mode registry-table boundary (load-bearing)
#
# This test locks the architectural invariant that the enqueue path NEVER
# consults the (future, phase-3) soniq_task_registry DB table. It mocks a
# backend.list_registered_task_names() that returns the name, and verifies
# that strict-mode enqueue still raises SONIQ_UNKNOWN_TASK_NAME because the
# *in-process* registry is empty. If a future refactor adds a fallback path
# that reads from the DB table, this test fails.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_strict_mode_does_not_consult_backend_registry_table():
    app = make_app(enqueue_validation="strict")
    backend_calls = []

    async def list_registered_task_names_spy():
        backend_calls.append("list_registered_task_names")
        return [{"task_name": "billing.from_db", "worker_id": "w1"}]

    # Initialize so we can patch the backend.
    await app._ensure_initialized()
    app._backend.list_registered_task_names = list_registered_task_names_spy  # type: ignore[attr-defined]

    with pytest.raises(SoniqError) as exc_info:
        await app.enqueue("billing.from_db", args={})

    assert exc_info.value.error_code == SONIQ_UNKNOWN_TASK_NAME
    # The boundary: enqueue must not call the registry-table reader.
    assert backend_calls == []


@pytest.mark.asyncio
async def test_app_module_does_not_import_registry_table_reader():
    """Cheap structural check: nothing in soniq/app.py mentions
    `list_registered_task_names`, so future contributors must edit this
    test (and read the boundary doc) to add a caller."""
    import inspect

    import soniq.app as app_mod

    src = inspect.getsource(app_mod)
    assert "list_registered_task_names" not in src, (
        "soniq/app.py must not reference list_registered_task_names; "
        "the registry table is observability only."
    )


# ---------------------------------------------------------------------------
# TaskRef arm
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enqueue_taskref_writes_row_using_ref_name():
    from soniq import task_ref

    app = make_app(enqueue_validation="strict")
    ref = task_ref(name="billing.taskref.foo")

    job_id = await app.enqueue(ref, args={"x": 1})
    rows = await app.list_jobs()
    row = next(r for r in rows if r["id"] == job_id)
    assert row["job_name"] == "billing.taskref.foo"
    assert row["args"] == {"x": 1}


@pytest.mark.asyncio
async def test_enqueue_taskref_skips_strict_registry_check():
    """The TaskRef arm short-circuits SONIQ_ENQUEUE_VALIDATION. Even in
    strict mode a TaskRef enqueue succeeds without registration."""
    from soniq import task_ref

    app = make_app(enqueue_validation="strict")
    ref = task_ref(name="billing.taskref.unregistered")

    # No @app.job for this name. The ref *is* the producer-side
    # declaration, so strict mode does not block.
    job_id = await app.enqueue(ref, args={})
    assert job_id


@pytest.mark.asyncio
async def test_enqueue_taskref_args_model_invalid_raises():
    from pydantic import BaseModel

    from soniq import task_ref

    class FooArgs(BaseModel):
        order_id: str

    app = make_app(enqueue_validation="strict")
    ref = task_ref(name="billing.taskref.invoice", args_model=FooArgs)

    with pytest.raises(SoniqError) as exc_info:
        await app.enqueue(ref, args={"order_id": 12345})  # int, not str
    assert exc_info.value.error_code == SONIQ_TASK_ARGS_INVALID

    rows = await app.list_jobs()
    assert not any(r["job_name"] == "billing.taskref.invoice" for r in rows)


@pytest.mark.asyncio
async def test_enqueue_taskref_args_model_valid_writes():
    from pydantic import BaseModel

    from soniq import task_ref

    class FooArgs(BaseModel):
        order_id: str

    app = make_app(enqueue_validation="strict")
    ref = task_ref(name="billing.taskref.valid", args_model=FooArgs)

    job_id = await app.enqueue(ref, args={"order_id": "o1"})
    assert job_id


@pytest.mark.asyncio
async def test_enqueue_taskref_queue_precedence_chain():
    """Queue precedence: explicit queue= > ref.default_queue > system
    default. Single test pinning all three sub-cases in one place so the
    chain cannot fragment across files (per todo_multi.md 2.2)."""
    from soniq import task_ref

    app = make_app(enqueue_validation="none")

    # Case 1: explicit queue= overrides ref.default_queue.
    ref_with_default = task_ref(name="billing.q.case1", default_queue="billing")
    job_id_1 = await app.enqueue(ref_with_default, args={}, queue="urgent")
    rows = await app.list_jobs()
    row = next(r for r in rows if r["id"] == job_id_1)
    assert row["queue"] == "urgent"

    # Case 2: ref.default_queue used when no explicit queue=.
    ref_with_default_2 = task_ref(name="billing.q.case2", default_queue="billing")
    job_id_2 = await app.enqueue(ref_with_default_2, args={})
    rows = await app.list_jobs()
    row = next(r for r in rows if r["id"] == job_id_2)
    assert row["queue"] == "billing"

    # Case 3: system default when ref has no default_queue and no
    # explicit queue=.
    ref_no_default = task_ref(name="billing.q.case3")
    job_id_3 = await app.enqueue(ref_no_default, args={})
    rows = await app.list_jobs()
    row = next(r for r in rows if r["id"] == job_id_3)
    assert row["queue"] == "default"


@pytest.mark.asyncio
async def test_enqueue_taskref_with_registered_handler_uses_ref_args_model():
    """When both the consumer's @app.job(validate=...) AND the producer's
    TaskRef(args_model=...) are present, the ref's model is the
    producer-side contract. We exercise this by registering a handler
    with a different (looser) model and watching the ref's model fail
    first."""
    from pydantic import BaseModel

    from soniq import task_ref

    class StrictArgs(BaseModel):
        order_id: str

    class LooserArgs(BaseModel):
        order_id: object  # accepts any type

    app = make_app(enqueue_validation="strict")

    @app.job(name="billing.taskref.both", validate=LooserArgs)
    async def handler(order_id):
        pass

    ref = task_ref(name="billing.taskref.both", args_model=StrictArgs)

    # int order_id passes the looser model but fails the strict ref.
    with pytest.raises(SoniqError) as exc_info:
        await app.enqueue(ref, args={"order_id": 123})
    assert exc_info.value.error_code == SONIQ_TASK_ARGS_INVALID


# ---------------------------------------------------------------------------
# enqueue_many
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enqueue_many_writes_all_rows(app):
    @app.job(name="billing.bulk")
    async def bulk(order_id: str):
        pass

    ids = await app.enqueue_many(
        "billing.bulk",
        [{"order_id": f"o{i}"} for i in range(5)],
    )
    assert len(ids) == 5
    assert len(set(ids)) == 5

    rows = await app.list_jobs()
    bulk_rows = [r for r in rows if r["job_name"] == "billing.bulk"]
    assert len(bulk_rows) == 5
    assert {r["args"]["order_id"] for r in bulk_rows} == {f"o{i}" for i in range(5)}


@pytest.mark.asyncio
async def test_enqueue_many_empty_list_returns_empty(app):
    @app.job(name="billing.empty")
    async def empty():
        pass

    ids = await app.enqueue_many("billing.empty", [])
    assert ids == []


@pytest.mark.asyncio
async def test_enqueue_many_accepts_callable(app):
    @app.job(name="billing.callable_bulk")
    async def handler(n: int):
        pass

    ids = await app.enqueue_many(handler, [{"n": i} for i in range(3)])
    assert len(ids) == 3


@pytest.mark.asyncio
async def test_enqueue_many_shared_options(app):
    @app.job(name="billing.opts", queue="default", priority=100)
    async def handler(n: int):
        pass

    await app.enqueue_many(
        "billing.opts",
        [{"n": 1}, {"n": 2}],
        queue="urgent",
        priority=10,
    )
    rows = await app.list_jobs()
    opts_rows = [r for r in rows if r["job_name"] == "billing.opts"]
    assert all(r["queue"] == "urgent" for r in opts_rows)
    assert all(r["priority"] == 10 for r in opts_rows)


@pytest.mark.asyncio
async def test_enqueue_many_validates_each_args_dict(app):
    from pydantic import BaseModel

    class Args(BaseModel):
        n: int

    @app.job(name="billing.validated_bulk", validate=Args)
    async def handler(n: int):
        pass

    with pytest.raises(SoniqError) as exc_info:
        await app.enqueue_many(
            "billing.validated_bulk",
            [{"n": 1}, {"n": "not-an-int"}],
        )
    assert exc_info.value.error_code == SONIQ_TASK_ARGS_INVALID
    assert exc_info.value.context.get("index") == 1


@pytest.mark.asyncio
async def test_enqueue_many_rejects_unique_jobs(app):
    @app.job(name="billing.unique_bulk", unique=True)
    async def handler(n: int):
        pass

    with pytest.raises(TypeError, match="unique"):
        await app.enqueue_many("billing.unique_bulk", [{"n": 1}])


@pytest.mark.asyncio
async def test_enqueue_many_rejects_non_dict_items(app):
    @app.job(name="billing.bad_items")
    async def handler():
        pass

    with pytest.raises(TypeError, match="must be a dict"):
        await app.enqueue_many("billing.bad_items", [{"n": 1}, "not-a-dict"])  # type: ignore[list-item]


@pytest.mark.asyncio
async def test_enqueue_many_strict_unknown_task_raises(app):
    with pytest.raises(SoniqError) as exc_info:
        await app.enqueue_many("billing.never_registered", [{}])
    assert exc_info.value.error_code == SONIQ_UNKNOWN_TASK_NAME


@pytest.mark.asyncio
async def test_enqueue_many_lenient_unknown_task_proceeds(lenient_app):
    ids = await lenient_app.enqueue_many(
        "billing.unknown_bulk",
        [{"x": 1}, {"x": 2}],
    )
    assert len(ids) == 2


@pytest.mark.asyncio
async def test_enqueue_many_taskref_validates_each_against_ref_model():
    from pydantic import BaseModel

    from soniq import task_ref

    class FooArgs(BaseModel):
        order_id: str

    app = make_app(enqueue_validation="none")
    ref = task_ref(name="billing.taskref.bulk", args_model=FooArgs)

    # All-valid path: writes every row.
    ids = await app.enqueue_many(ref, [{"order_id": "o1"}, {"order_id": "o2"}])
    assert len(ids) == 2

    # One-bad row: nothing is written, error pinpoints the index.
    with pytest.raises(SoniqError) as exc_info:
        await app.enqueue_many(
            ref,
            [{"order_id": "o3"}, {"order_id": 999}],  # int -> ValidationError
        )
    assert exc_info.value.error_code == SONIQ_TASK_ARGS_INVALID
    assert exc_info.value.context.get("index") == 1

    rows = await app.list_jobs()
    bulk_rows = [r for r in rows if r["job_name"] == "billing.taskref.bulk"]
    assert len(bulk_rows) == 2  # only the all-valid call wrote rows
    assert {r["args"]["order_id"] for r in bulk_rows} == {"o1", "o2"}


@pytest.mark.asyncio
async def test_enqueue_many_scheduled_at_applied_to_every_row(app):
    from datetime import datetime, timedelta, timezone

    @app.job(name="billing.scheduled_bulk")
    async def handler(n: int):
        pass

    run_at = datetime.now(timezone.utc) + timedelta(hours=1)
    ids = await app.enqueue_many(
        "billing.scheduled_bulk",
        [{"n": i} for i in range(3)],
        scheduled_at=run_at,
    )
    assert len(ids) == 3

    rows = await app.list_jobs()
    scheduled_rows = [r for r in rows if r["job_name"] == "billing.scheduled_bulk"]
    assert len(scheduled_rows) == 3
    for r in scheduled_rows:
        # Memory backend stores the datetime directly, not an ISO string.
        ts = r["scheduled_at"]
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)
        assert abs((ts - run_at).total_seconds()) < 1


@pytest.mark.asyncio
async def test_enqueue_many_args_list_not_a_list_raises(app):
    @app.job(name="billing.bad_args_list")
    async def handler():
        pass

    with pytest.raises(TypeError, match="args_list must be a list"):
        await app.enqueue_many("billing.bad_args_list", {"n": 1})  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_enqueue_many_invalid_target_type_raises(app):
    with pytest.raises(TypeError, match="must be a callable, string, or TaskRef"):
        await app.enqueue_many(12345, [{}])  # type: ignore[arg-type]
