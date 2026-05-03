"""
Task name validation.

Centralised helper for validating task names against a configured
task_name_pattern. Called at @app.job(name=) registration and at
enqueue() call time so that one rule governs both ends of the wire.
"""

import re

from soniq.errors import SONIQ_INVALID_TASK_NAME, SoniqError


def validate_task_name(name: object, pattern: str) -> str:
    """Return `name` if it matches `pattern`; raise otherwise.

    `pattern` is required: the caller threads its instance's
    `SoniqSettings.task_name_pattern` (or, at module-scope factories
    with no instance handy, constructs a fresh `SoniqSettings()`). No
    runtime `get_settings()` lookup happens here - that would couple
    every validation to a process-global cache and violate the
    instance-boundary contract (`docs/_internals/contracts/instance_boundary.md`).

    Raises `SoniqError(SONIQ_INVALID_TASK_NAME)` on a non-string `name` or
    on a pattern mismatch. The error includes the offending name and the
    pattern in `context` so dashboards can render them without re-parsing
    the message.
    """
    if not isinstance(name, str):
        raise SoniqError(
            f"task name must be a string, got {type(name).__name__}",
            SONIQ_INVALID_TASK_NAME,
            context={"received_type": type(name).__name__},
        )
    if not re.fullmatch(pattern, name):
        raise SoniqError(
            f"task name {name!r} does not match SONIQ_TASK_NAME_PATTERN "
            f"({pattern!r})",
            SONIQ_INVALID_TASK_NAME,
            context={"name": name, "pattern": pattern},
            suggestions=[
                "Use a dotted lowercase identifier (e.g. 'billing.invoices.send.v2').",
                "Override SONIQ_TASK_NAME_PATTERN if your project uses a different convention.",
            ],
        )
    return name
