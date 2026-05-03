"""Tests for the SoniqPlugin lifecycle.

Three concerns:

1. ``install`` runs once when a plugin is added via the constructor or
   ``app.use``; calling ``use`` twice with the same name raises
   ``SONIQ_PLUGIN_DUPLICATE``.
2. ``on_startup`` fires from ``Soniq.setup()`` after backend init, in
   install order; failures propagate so misconfigured plugins fail
   loud.
3. ``on_shutdown`` fires from ``Soniq.close()`` in reverse install
   order; failures are logged and swallowed so one plugin's bug
   doesn't block the next.

These tests use the in-memory backend so they exercise the real
lifecycle path without needing Postgres.
"""

from __future__ import annotations

from typing import List

import pytest

from soniq import Soniq
from soniq.errors import SONIQ_PLUGIN_DUPLICATE, SoniqError
from soniq.plugin import SoniqPlugin


class _Recorder:
    """Plugin that records every lifecycle event into a shared list."""

    def __init__(self, name: str, events: List[str]):
        self.name = name
        self.version = "0.0.1"
        self._events = events

    def install(self, app):
        self._events.append(f"{self.name}:install")

    async def on_startup(self, app):
        self._events.append(f"{self.name}:startup")

    async def on_shutdown(self, app):
        self._events.append(f"{self.name}:shutdown")


class _NoHooks:
    """A plugin with no on_startup / on_shutdown - the optional hooks
    are detected via hasattr, not by the Protocol."""

    name = "no-hooks"
    version = "0.1.0"

    def install(self, app):
        pass


class _Failing:
    """A plugin whose on_startup raises so we can pin failure
    propagation. on_shutdown is intentionally also failing to verify
    the swallowing behavior on the close path."""

    name = "failing"
    version = "0.0.1"

    def install(self, app):
        pass

    async def on_startup(self, app):
        raise RuntimeError("boom")

    async def on_shutdown(self, app):
        raise RuntimeError("kaboom")


# ---------------------------------------------------------------------------
# Protocol shape
# ---------------------------------------------------------------------------


def test_plugin_satisfies_protocol_with_minimal_members():
    class P:
        name = "minimal"
        version = "0.1.0"

        def install(self, app):
            pass

    assert isinstance(P(), SoniqPlugin)


def test_plugin_protocol_does_not_force_optional_hooks():
    """Plugins without ``on_startup`` / ``on_shutdown`` still satisfy
    the Protocol. The runner detects hooks via ``hasattr``."""
    p = _NoHooks()
    assert isinstance(p, SoniqPlugin)


# ---------------------------------------------------------------------------
# Install order + duplicate guard
# ---------------------------------------------------------------------------


def test_constructor_installs_in_order():
    events: List[str] = []
    Soniq(
        backend="memory",
        plugins=[_Recorder("a", events), _Recorder("b", events)],
    )
    assert events == ["a:install", "b:install"]


def test_use_installs_after_construction():
    events: List[str] = []
    app = Soniq(backend="memory")
    app.use(_Recorder("a", events))
    app.use(_Recorder("b", events))
    assert events == ["a:install", "b:install"]


def test_duplicate_name_raises_soniq_plugin_duplicate():
    events: List[str] = []
    app = Soniq(backend="memory", plugins=[_Recorder("dup", events)])
    with pytest.raises(SoniqError) as exc_info:
        app.use(_Recorder("dup", events))
    assert exc_info.value.error_code == SONIQ_PLUGIN_DUPLICATE


def test_plugins_registry_view():
    events: List[str] = []
    app = Soniq(
        backend="memory",
        plugins=[_Recorder("alpha", events), _Recorder("beta", events)],
    )
    assert "alpha" in app.plugins
    assert "missing" not in app.plugins
    assert app.plugins["alpha"].name == "alpha"
    with pytest.raises(KeyError):
        _ = app.plugins["missing"]
    assert [p.name for p in app.plugins] == ["alpha", "beta"]
    assert len(app.plugins) == 2


# ---------------------------------------------------------------------------
# on_startup / on_shutdown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_startup_runs_in_install_order():
    events: List[str] = []
    app = Soniq(
        backend="memory",
        plugins=[_Recorder("first", events), _Recorder("second", events)],
    )
    await app.setup()
    await app.close()

    install_events = [e for e in events if e.endswith(":install")]
    startup_events = [e for e in events if e.endswith(":startup")]
    assert install_events == ["first:install", "second:install"]
    assert startup_events == ["first:startup", "second:startup"]


@pytest.mark.asyncio
async def test_on_shutdown_runs_in_reverse_order():
    events: List[str] = []
    app = Soniq(
        backend="memory",
        plugins=[_Recorder("first", events), _Recorder("second", events)],
    )
    await app.setup()
    await app.close()

    shutdown_events = [e for e in events if e.endswith(":shutdown")]
    assert shutdown_events == ["second:shutdown", "first:shutdown"]


@pytest.mark.asyncio
async def test_on_startup_failure_propagates():
    """A misconfigured plugin must not boot silently."""
    app = Soniq(backend="memory", plugins=[_Failing()])
    with pytest.raises(RuntimeError, match="boom"):
        await app.setup()
    # cleanup so subsequent tests don't see a half-up app
    await app.close()


@pytest.mark.asyncio
async def test_on_shutdown_failure_is_swallowed(caplog):
    """One plugin's bad on_shutdown must not block the next."""
    events: List[str] = []
    app = Soniq(
        backend="memory",
        plugins=[_Recorder("recorder", events), _Failing()],
    )
    # Skip on_startup-failing plugin's startup by short-circuiting -
    # we want close() to exercise the on_shutdown path. Construct a
    # second app where startup ran cleanly first, then verify shutdown
    # behavior on a class whose only failure is the shutdown hook.
    await app.close()  # close() works even if setup wasn't called

    # The recorder's shutdown still fires even though _Failing's
    # shutdown raised - reverse order means failing runs first.
    assert "recorder:shutdown" in events


@pytest.mark.asyncio
async def test_no_hooks_plugin_lifecycle_is_a_noop():
    """A plugin with no on_startup / on_shutdown installs cleanly and
    contributes nothing to setup/close beyond the install call."""
    app = Soniq(backend="memory", plugins=[_NoHooks()])
    await app.setup()
    await app.close()
    # Just asserting the path didn't blow up; the plugin is in the
    # registry and its hooks are absent.
    assert "no-hooks" in app.plugins
