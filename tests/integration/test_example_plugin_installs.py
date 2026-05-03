"""End-to-end smoke test for the example plugin.

The ``examples/plugins/sentry_breadcrumb`` package is installed in
editable mode by the test environment and registered via the
``soniq.plugins`` entry-point group. This test verifies:

1. Entry-point discovery resolves the package by name.
2. ``Soniq(plugins=[plugin])`` and ``Soniq(autoload_plugins=True)`` both
   install it.
3. The plugin's ``install()`` registers a CLI command via
   ``app.cli.add_command``, which means the public extension point
   plumbs through to argparse end-to-end.
4. ``await app.setup()`` runs ``on_startup`` cleanly when no DSN is
   configured (the plugin runs inert) and ``await app.close()`` runs
   ``on_shutdown`` without raising.

Plugin contract dogfood: this test is the regression net that catches
"the contract is missing something a real plugin needs" before a
third-party author files a bug. Keep it green by widening the public
surface, not by reaching for private names from the example.
"""

from __future__ import annotations

import importlib.util

import pytest

from soniq import Soniq
from soniq.cli.main import build_parser
from soniq.plugin import discover_plugins

# The example plugin is installed editable in CI. Skip if absent so a
# fresh checkout without `pip install examples/plugins/sentry_breadcrumb`
# doesn't fail the entire integration suite.
pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("sentry_breadcrumb") is None,
    reason="sentry_breadcrumb example plugin not installed; run "
    "`pip install -e examples/plugins/sentry_breadcrumb` first",
)


def test_discover_plugins_finds_example_plugin():
    plugins = discover_plugins(["sentry_breadcrumb"])
    assert len(plugins) == 1
    assert plugins[0].name == "sentry_breadcrumb"
    assert plugins[0].version == "0.1.0"


def test_install_via_constructor():
    from sentry_breadcrumb import SentryBreadcrumbPlugin

    app = Soniq(backend="memory", plugins=[SentryBreadcrumbPlugin()])
    assert "sentry_breadcrumb" in app.plugins


def test_autoload_plugins_loads_example():
    app = Soniq(backend="memory", autoload_plugins=True)
    assert "sentry_breadcrumb" in app.plugins


def test_plugin_registers_cli_command():
    """The plugin's install adds a `sentry-test` subcommand."""
    from sentry_breadcrumb import SentryBreadcrumbPlugin

    app = Soniq(backend="memory", plugins=[SentryBreadcrumbPlugin()])
    parser = build_parser(plugin_app=app)
    import argparse

    sub_action = next(
        a for a in parser._actions if isinstance(a, argparse._SubParsersAction)
    )
    assert "sentry-test" in sub_action.choices


@pytest.mark.asyncio
async def test_lifecycle_runs_clean_without_dsn(monkeypatch):
    """With no SONIQ_SENTRY_DSN set, the plugin runs inert:
    ``on_startup`` initializes nothing and ``on_shutdown`` is a no-op.
    Pinning so the example stays installable in CI without a Sentry
    project."""
    monkeypatch.delenv("SONIQ_SENTRY_DSN", raising=False)
    from sentry_breadcrumb import SentryBreadcrumbPlugin

    app = Soniq(backend="memory", plugins=[SentryBreadcrumbPlugin()])
    await app.setup()
    await app.close()
    assert "sentry_breadcrumb" in app.plugins


@pytest.mark.asyncio
async def test_plugin_middleware_wraps_jobs(monkeypatch):
    """The plugin registers a middleware via app.middleware. End-to-end
    proof: enqueue a job, run the worker once, and confirm the job
    completed (the middleware is a no-op breadcrumb without a DSN, but
    the chain must still wrap and call through)."""
    monkeypatch.delenv("SONIQ_SENTRY_DSN", raising=False)
    from sentry_breadcrumb import SentryBreadcrumbPlugin

    app = Soniq(backend="memory", plugins=[SentryBreadcrumbPlugin()])
    ran = []

    @app.job(name="demo.echo")
    async def echo(value: str) -> None:
        ran.append(value)

    await app.setup()
    await app.enqueue("demo.echo", args={"value": "ping"})
    await app.run_worker(run_once=True)
    await app.close()

    assert ran == ["ping"]
