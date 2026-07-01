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
from typing import Any, AsyncIterator, List
from urllib.parse import urlsplit

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


_DEFAULT_DB_PORTS = {"postgresql": 5432}


def _normalize_db_url(url: str) -> tuple:
    """Reduce a database URL to the parts that decide *which* database it hits.

    Folds away differences that don't change the target: the
    ``postgres``/``postgresql`` scheme spelling, host casing, an explicit
    default port, and a trailing slash on the database path. This only feeds an
    equivalence check, so best-effort is enough - an unparseable URL falls back
    to a trimmed string compare.
    """
    try:
        parts = urlsplit(url or "")
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        return ((url or "").rstrip("/"),)
    scheme = (
        "postgresql" if parts.scheme in ("postgres", "postgresql") else parts.scheme
    )
    if port is None:
        port = _DEFAULT_DB_PORTS.get(scheme)
    path = (parts.path or "").rstrip("/")
    return (scheme, parts.username, parts.password, host, port, path, parts.query)


def _database_urls_differ(a: str, b: str) -> bool:
    return _normalize_db_url(a) != _normalize_db_url(b)


@asynccontextmanager
async def execution_app(args: Any, modules: List[str]) -> AsyncIterator[Soniq]:
    """Yield the Soniq instance a worker / scheduler should run on.

    Unlike ``cli_app``, execution commands must run the *same* instance the job
    modules registered their handlers against - a fresh instance would have an
    empty registry and dead-letter every job as "not registered". We import the
    modules, find the instance they define, and use it. If the modules define no
    instance, we fall back to a fresh one (``cli_app`` behaviour).

    ``modules`` must already have been imported (via
    ``discover_and_import_modules``) before this is called.
    """
    from soniq.discovery import find_soniq_app

    discovered = find_soniq_app(modules)
    if discovered is None:
        async with cli_app(args) as app:
            yield app
        return

    # A discovered instance owns its own backend/settings. If the caller also
    # passed --database-url pointing at a *different* database, we can't honour
    # both: the jobs live on the discovered instance's database, and the flag
    # can't be re-applied to an already-constructed instance. Silently ignoring
    # it would point the worker/scheduler at the wrong place with no signal, so
    # fail loudly and let the operator resolve the conflict.
    flag_url = getattr(args, "database_url", None)
    if flag_url and _database_urls_differ(flag_url, discovered.settings.database_url):
        print_status(
            f"--database-url ({flag_url}) conflicts with the database your job "
            f"modules' Soniq instance connects to ({discovered.settings.database_url}). "
            "A worker/scheduler runs on the job-module instance, so --database-url "
            "can't be applied. Remove the flag, or point it at the same database.",
            "error",
        )
        raise SystemExit(1)
    print_status(
        f"Using job-module instance: {discovered.settings.database_url}",
        "info",
    )
    try:
        yield discovered
    finally:
        if discovered.is_initialized:
            await discovered.close()
