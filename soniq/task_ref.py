"""
TaskRef: a typed, dumb constant identifying a task by name.

A `TaskRef` is what producers import from a shared stub package to enqueue
into a service that owns the implementation. It is intentionally small:
a frozen, slotted, hashable dataclass with three fields and one helper.
No runtime imports, no proxy generation, no network lookups. A reader
of a `TaskRef` declaration can predict the exact bytes that land on the
queue.

The shape:

    @dataclass(frozen=True, slots=True)
    class TaskRef:
        name: str
        args_model: type | None = None
        default_queue: str | None = None

The `task_ref(...)` factory validates the name against the configured
`SONIQ_TASK_NAME_PATTERN` so a typo at declaration time raises rather
than landing in production.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Optional, Type

from .core.naming import validate_task_name
from .settings import SoniqSettings


@dataclasses.dataclass(frozen=True, slots=True)
class TaskRef:
    """A typed constant pointing at a registered task by name.

    Pass to ``Soniq.enqueue`` exactly like a string name. When ``args_model``
    is set, the producer-side ``args`` dict is validated against it before
    the row is written. When ``default_queue`` is set and the call did not
    pass an explicit ``queue=``, the row is enqueued onto that queue.

    Frozen so it can be a dict key; slotted so attribute access is cheap and
    accidental attribute writes raise. ``__repr__`` prints exactly the
    fields - no module path, no callable, no proxy.
    """

    name: str
    args_model: Optional[Type[Any]] = None
    default_queue: Optional[str] = None

    def with_default_queue(self, queue: str) -> "TaskRef":
        """Return a new ``TaskRef`` with ``default_queue`` set to ``queue``.

        ``TaskRef`` is frozen, so this is the supported way to specialise a
        ref's routing without re-declaring it. Three lines of mechanical
        ``dataclasses.replace`` so the original ref is unchanged.
        """
        return dataclasses.replace(self, default_queue=queue)


def task_ref(
    *,
    name: str,
    args_model: Optional[Type[Any]] = None,
    default_queue: Optional[str] = None,
    pattern: Optional[str] = None,
) -> TaskRef:
    """Construct a validated ``TaskRef``.

    The name is checked against ``SONIQ_TASK_NAME_PATTERN`` at construction
    time so a typo in a stub package fails at import / first use rather
    than as a dead-letter row in production.

    ``pattern`` lets callers thread an explicit pattern (e.g. from a Soniq
    instance's settings). When omitted, a fresh ``SoniqSettings()`` is
    constructed to read the env-configured pattern - we never consult the
    cached global (`docs/_internals/contracts/instance_boundary.md`).
    """
    if pattern is None:
        pattern = SoniqSettings().task_name_pattern
    validate_task_name(name, pattern)
    return TaskRef(name=name, args_model=args_model, default_queue=default_queue)
