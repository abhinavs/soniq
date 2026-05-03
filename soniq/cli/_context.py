"""
Unified CLI app resolution + lifecycle.

Every subcommand that talks to a Soniq instance goes through ``cli_app``:

    async with cli_app(args) as app:
        ...

``cli_app`` always builds a fresh Soniq instance scoped to this CLI
invocation. URL precedence: ``--database-url`` > ``$SONIQ_DATABASE_URL``
> the default in ``SoniqSettings``. There is no fallback to a
process-global instance - per ``docs/_internals/contracts/instance_boundary.md``,
the only state allowed to be process-global in 0.0.2 is logging
configuration.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from soniq import Soniq

from ._helpers import resolve_soniq_instance
from .colors import print_status


async def resolve_app(args: Any) -> Soniq:
    """Resolve the Soniq instance for a CLI subcommand.

    Returns a freshly constructed ``Soniq`` scoped to this invocation.
    Callers are responsible for closing it (``cli_app`` does this on
    context exit).
    """
    instance = await resolve_soniq_instance(args)
    if instance is not None:
        return instance
    return Soniq()


@asynccontextmanager
async def cli_app(args: Any) -> AsyncIterator[Soniq]:
    """Yield a Soniq instance for a CLI subcommand.

    Always builds a fresh instance and closes it on exit.
    """
    app = await resolve_app(args)
    print_status(
        f"Using instance-based configuration: {app.settings.database_url}",
        "info",
    )
    try:
        yield app
    finally:
        if app.is_initialized:
            await app.close()
