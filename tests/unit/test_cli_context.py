"""
Unit tests for the unified CLI app helper.

``soniq.cli._context.cli_app`` and ``resolve_app`` are the entry points
every CLI subcommand uses. The helper always builds a fresh Soniq scoped
to this invocation and closes it on context-manager exit. There is no
fallback to a process-global instance (see
``docs/_internals/contracts/instance_boundary.md``).
"""

from __future__ import annotations

import argparse

import pytest

from soniq.cli._context import cli_app, resolve_app


@pytest.mark.asyncio
async def test_resolve_app_uses_default_settings_when_no_flag(monkeypatch):
    """No ``--database-url`` -> a Soniq built from default settings."""
    monkeypatch.delenv("SONIQ_DATABASE_URL", raising=False)
    args = argparse.Namespace(database_url=None)
    app = await resolve_app(args)

    from soniq import Soniq

    assert isinstance(app, Soniq)


@pytest.mark.asyncio
async def test_resolve_app_builds_fresh_instance_from_flag():
    """``--database-url`` -> fresh Soniq with that URL."""
    args = argparse.Namespace(
        database_url="postgresql://u:p@localhost:5432/explicit_db"
    )
    app = await resolve_app(args)

    assert app.settings.database_url == "postgresql://u:p@localhost:5432/explicit_db"


@pytest.mark.asyncio
async def test_cli_app_closes_instance_on_exit(monkeypatch):
    """The instance is always closed on exit. Spy on Soniq.close to
    avoid needing a real database."""
    from soniq import Soniq

    closed: list[Soniq] = []

    async def fake_close(self):
        closed.append(self)

    monkeypatch.setattr(Soniq, "close", fake_close)
    monkeypatch.setattr(Soniq, "is_initialized", property(lambda self: True))

    args = argparse.Namespace(database_url="postgresql://u:p@localhost:5432/owned_db")

    async with cli_app(args) as app:
        assert isinstance(app, Soniq)

    assert closed == [app], "The CLI-owned instance should be closed on exit."
