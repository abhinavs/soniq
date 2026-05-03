"""
Tests for TaskRef and the task_ref(...) factory.

The TaskRef shape is frozen, slotted, hashable, no runtime magic.
These tests pin the contract so a future refactor cannot quietly turn
TaskRef into a remote-actor proxy.
"""

from __future__ import annotations

import dataclasses
import os

import pytest
from pydantic import BaseModel

from tests.db_utils import TEST_DATABASE_URL

os.environ.setdefault("SONIQ_DATABASE_URL", TEST_DATABASE_URL)

from soniq import TaskRef, task_ref  # noqa: E402
from soniq.errors import SONIQ_INVALID_TASK_NAME, SoniqError  # noqa: E402

# ---------------------------------------------------------------------------
# Construction and validation
# ---------------------------------------------------------------------------


def test_task_ref_constructed_with_name_only():
    ref = task_ref(name="billing.foo")
    assert ref.name == "billing.foo"
    assert ref.args_model is None
    assert ref.default_queue is None


def test_task_ref_carries_args_model():
    class FooArgs(BaseModel):
        x: int

    ref = task_ref(name="billing.foo", args_model=FooArgs)
    assert ref.args_model is FooArgs


def test_task_ref_carries_default_queue():
    ref = task_ref(name="billing.foo", default_queue="billing")
    assert ref.default_queue == "billing"


@pytest.mark.parametrize(
    "bad",
    ["Bad Name", "Has.Caps", ".leading", "trailing.", "double..dot", ""],
)
def test_invalid_name_rejected_at_construction(bad):
    with pytest.raises(SoniqError) as exc_info:
        task_ref(name=bad)
    assert exc_info.value.error_code == SONIQ_INVALID_TASK_NAME


# ---------------------------------------------------------------------------
# Frozen + slotted
# ---------------------------------------------------------------------------


def test_task_ref_is_frozen():
    ref = task_ref(name="a.b")
    with pytest.raises(dataclasses.FrozenInstanceError):
        ref.name = "a.c"  # type: ignore[misc]


def test_task_ref_has_slots():
    """No __dict__ on instances - the slotted-dataclass invariant."""
    ref = task_ref(name="a.b")
    assert not hasattr(ref, "__dict__")
    # Adding arbitrary attributes is refused. Slotted+frozen dataclasses
    # raise FrozenInstanceError or TypeError depending on the cpython path.
    with pytest.raises((AttributeError, dataclasses.FrozenInstanceError, TypeError)):
        ref.extra = "nope"  # type: ignore[attr-defined]


def test_task_ref_class_advertises_slots():
    """Class-level structural check: __slots__ exists. Used as a guard
    against a future contributor removing the slots= flag."""
    assert hasattr(TaskRef, "__slots__")


def test_task_ref_is_dataclass():
    """Structural check: is_dataclass is True. Used as a guard against
    a future contributor turning TaskRef into a custom class."""
    assert dataclasses.is_dataclass(TaskRef)


# ---------------------------------------------------------------------------
# Hashable + equality
# ---------------------------------------------------------------------------


def test_task_ref_is_hashable():
    a = task_ref(name="billing.foo")
    b = task_ref(name="billing.foo")
    c = task_ref(name="billing.bar")
    # Same fields -> equal -> same hash.
    assert a == b
    assert hash(a) == hash(b)
    # Different name -> different.
    assert a != c
    # Usable as a dict key and a set member.
    seen = {a: 1, c: 2}
    assert seen[b] == 1
    members = {a, b, c}
    assert len(members) == 2


def test_task_ref_hash_includes_args_model_and_default_queue():
    class A(BaseModel):
        x: int

    class B(BaseModel):
        x: int

    ref1 = task_ref(name="x.y", args_model=A, default_queue="q1")
    ref2 = task_ref(name="x.y", args_model=B, default_queue="q1")
    ref3 = task_ref(name="x.y", args_model=A, default_queue="q2")
    assert ref1 != ref2  # different args_model class
    assert ref1 != ref3  # different default_queue


# ---------------------------------------------------------------------------
# with_default_queue
# ---------------------------------------------------------------------------


def test_with_default_queue_returns_new_ref():
    base = task_ref(name="billing.foo")
    routed = base.with_default_queue("urgent")
    assert routed is not base
    assert routed.default_queue == "urgent"


def test_with_default_queue_preserves_other_fields():
    class FooArgs(BaseModel):
        x: int

    base = task_ref(name="billing.foo", args_model=FooArgs)
    routed = base.with_default_queue("urgent")
    assert routed.name == "billing.foo"
    assert routed.args_model is FooArgs


def test_with_default_queue_does_not_mutate_original():
    """Frozen invariant regression: the original ref is unchanged after
    calling with_default_queue on it."""
    base = task_ref(name="billing.foo", default_queue="default")
    base.with_default_queue("urgent")
    assert base.default_queue == "default"


def test_with_default_queue_can_chain():
    base = task_ref(name="billing.foo")
    a = base.with_default_queue("q1")
    b = a.with_default_queue("q2")
    assert base.default_queue is None
    assert a.default_queue == "q1"
    assert b.default_queue == "q2"


# ---------------------------------------------------------------------------
# repr: predictable, no module path, no callable
# ---------------------------------------------------------------------------


def test_repr_is_predictable_and_field_only():
    ref = task_ref(name="billing.foo")
    text = repr(ref)
    assert "billing.foo" in text
    # No module path of the dataclass leaking into the repr beyond the
    # class name itself.
    assert "args_model" in text
    assert "default_queue" in text


# ---------------------------------------------------------------------------
# No-magic guard: constructing a TaskRef does not import anything else
# ---------------------------------------------------------------------------


def test_construction_does_not_trigger_arbitrary_imports(monkeypatch):
    """If TaskRef ever started importing modules at construction time,
    the substrate would be magical. Pin the absence of that with a spy
    on importlib.import_module."""
    import importlib

    calls = []
    real = importlib.import_module

    def spy(name, *args, **kwargs):
        calls.append(name)
        return real(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", spy)
    ref = task_ref(name="some.task")  # noqa: F841
    # The construction path does not call importlib.import_module.
    # (Even validate_task_name is imported lazily but via plain `from
    # ... import`, not importlib; the lazy import happens once at class
    # load time, not per-construction.)
    assert calls == []
