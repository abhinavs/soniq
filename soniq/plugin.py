"""
Plugin contract for Soniq.

A plugin is anything that satisfies the ``SoniqPlugin`` Protocol. The
contract is intentionally lean: plugins compose by calling Soniq's
**public** API only - the same surface a user has. If a plugin needs
something the public surface can't express, that's the signal to widen
the public surface for everyone, not to add a private door for plugins.

Public extension points a plugin can use:

- ``app.middleware(fn)``                  - wrap every job
- ``app.before_job(fn)`` / ``after_job`` / ``on_error`` - lifecycle hooks
- ``app.job(name=..., ...)``              - register handlers
- ``app.scheduler.add(...)``              - schedule recurring work
- ``app.metrics_sink = sink``             - replace metrics destination
- ``app.cli.add_command(spec)``           - add a ``soniq <name>`` subcommand
- ``app.dashboard.add_panel(spec)``       - add a dashboard panel
- ``app.migrations.register_source(...)`` - ship plugin-owned tables

Plugins MUST NOT touch underscore-prefixed attributes on ``Soniq``,
``StorageBackend``, or any other class. Those are private and may
change without notice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib.metadata import entry_points
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    Iterator,
    List,
    Optional,
    Protocol,
    Union,
    runtime_checkable,
)

if TYPE_CHECKING:
    from .app import Soniq


@runtime_checkable
class SoniqPlugin(Protocol):
    """A Soniq plugin.

    Implementations declare a ``name`` and SemVer ``version``, then wire
    themselves into the application via ``install(app)``.

    Two optional async hooks - ``on_startup(app)`` and
    ``on_shutdown(app)`` - fire from ``Soniq.setup()`` and
    ``Soniq.close()`` for plugins that need deferred I/O (open a
    connection, register a remote webhook, ...). They are intentionally
    *not* declared on this Protocol so plugins that don't need them
    don't have to define no-op stubs; the runner checks for them with
    ``hasattr`` at lifecycle time.

    ``on_startup`` failures propagate (a misconfigured plugin must not
    boot silently). ``on_shutdown`` failures are logged and swallowed
    so one plugin's bug can't block the next from running.
    """

    name: str
    version: str

    def install(self, app: "Soniq") -> None:
        """Synchronously wire the plugin into ``app``.

        Called once, by ``app.use(plugin)`` or ``Soniq(plugins=[...])``.
        Must not perform I/O - defer DB / network setup to
        ``on_startup``. Plugins typically register a middleware, a few
        hooks, maybe a CLI command, maybe a dashboard panel here.
        """
        ...


# ---------------------------------------------------------------------------
# Read-only registry view
# ---------------------------------------------------------------------------


class PluginRegistry:
    """Read-only view over installed plugins.

    Returned by ``app.plugins``. Supports ``app.plugins["name"]`` lookup,
    ``"name" in app.plugins``, iteration, and ``len()``. Plugin-aware
    code uses this to ask "is plugin X installed and at what version?"
    """

    def __init__(self, plugins: List[SoniqPlugin]):
        self._plugins = plugins

    def __getitem__(self, name: str) -> SoniqPlugin:
        for p in self._plugins:
            if p.name == name:
                return p
        raise KeyError(name)

    def __contains__(self, name: object) -> bool:
        return any(p.name == name for p in self._plugins)

    def __iter__(self) -> Iterator[SoniqPlugin]:
        return iter(self._plugins)

    def __len__(self) -> int:
        return len(self._plugins)

    def list(self) -> List[SoniqPlugin]:
        """Return a fresh list of installed plugins in install order."""
        return list(self._plugins)


# ---------------------------------------------------------------------------
# CLI extension point
# ---------------------------------------------------------------------------


@dataclass
class CommandSpec:
    """A CLI subcommand registered by a plugin.

    Plugins build one of these in ``install()`` and pass it to
    ``app.cli.add_command``. The CLI's parser builder picks them up
    after the built-in subcommands.

    ``handler`` may be sync or async; ``main`` dispatches accordingly.
    ``arguments`` is a list of ``{"args": [...], "kwargs": {...}}``
    dicts forwarded to ``argparse.ArgumentParser.add_argument`` so
    plugins don't have to import argparse themselves.
    """

    name: str
    help: str
    handler: Callable[..., Any]
    description: Optional[str] = None
    arguments: List[dict[str, Any]] = field(default_factory=list)


class PluginCLI:
    """Plugin-facing handle for ``app.cli.add_command``.

    Stays intentionally minimal - plugins only need to register
    commands. The CLI's main module reads ``_commands`` directly to
    build the argparse subparsers; that attribute is internal to the
    CLI <-> plugin contract.
    """

    def __init__(self) -> None:
        self._commands: List[CommandSpec] = []

    def add_command(self, spec: CommandSpec) -> None:
        """Register a CLI subcommand. Names must not collide with the
        built-ins (start, setup, status, ...) - argparse will raise on
        the second registration if they do."""
        self._commands.append(spec)


# ---------------------------------------------------------------------------
# Dashboard extension point
# ---------------------------------------------------------------------------


@dataclass
class PanelSpec:
    """A dashboard panel registered by a plugin.

    ``render`` is awaited at request time and returns either an HTML
    fragment (``str``) or a JSON-serializable ``dict``. The dashboard
    server iterates registered panels alongside the built-ins.
    """

    id: str
    title: str
    render: Callable[["Soniq"], Awaitable[Union[dict[str, Any], str]]]


class PluginDashboard:
    """Plugin-facing handle for ``app.dashboard.add_panel``."""

    def __init__(self) -> None:
        self._panels: List[PanelSpec] = []

    def add_panel(self, spec: PanelSpec) -> None:
        """Register a dashboard panel. Panel IDs must be unique within
        the application; collisions raise ``ValueError`` at registration
        rather than rendering two panels with the same DOM id."""
        if any(p.id == spec.id for p in self._panels):
            raise ValueError(f"Dashboard panel id {spec.id!r} is already registered")
        self._panels.append(spec)


# ---------------------------------------------------------------------------
# Migration source registration
# ---------------------------------------------------------------------------


@dataclass
class MigrationSource:
    """A plugin-owned directory of ``.sql`` migration files.

    ``prefix`` is a 4-digit string (``"0100"`` ... ``"9999"``) used as
    the version namespace so the runner can sort plugin migrations
    deterministically alongside core (``0001``-``0099``). Reserved
    ranges - see ``docs/plugins/authoring.md``:

    - ``0001``-``0099`` core
    - ``0100``-``8999`` OSS plugins
    - ``9000``-``9999`` reserved (commercial / soniq-pro)
    """

    path: Path
    prefix: str


class PluginMigrations:
    """Plugin-facing handle for ``app.migrations.register_source``.

    The migration runner reads ``_sources`` at ``app.setup()`` time to
    discover plugin migrations.
    """

    def __init__(self) -> None:
        self._sources: List[MigrationSource] = []

    def register_source(self, path: Union[str, Path], prefix: str) -> None:
        """Register a directory of plugin-owned migrations.

        ``path`` is the directory containing ``NNNN_*.sql`` files;
        ``prefix`` is the 4-digit version namespace.
        """
        if not (len(prefix) == 4 and prefix.isdigit()):
            raise ValueError(
                f"Migration prefix must be a 4-digit string, got {prefix!r}"
            )
        self._sources.append(MigrationSource(path=Path(path), prefix=prefix))

    def list_sources(self) -> List[MigrationSource]:
        """Return registered plugin migration sources."""
        return list(self._sources)


# ---------------------------------------------------------------------------
# Entry-point discovery
# ---------------------------------------------------------------------------


_ENTRY_POINT_GROUP = "soniq.plugins"


def discover_plugins(names: Optional[List[str]] = None) -> List[SoniqPlugin]:
    """Resolve plugins from the ``soniq.plugins`` entry-point group.

    Each entry point is expected to resolve to a zero-arg callable
    returning a ``SoniqPlugin`` instance (a class works, since classes
    are zero-arg callables when their ``__init__`` allows it).

    When ``names`` is ``None``, every entry point in the group is
    loaded; otherwise only the named entries. Unknown names raise
    ``KeyError`` so a typo in ``--plugins=stripe`` fails loudly rather
    than silently loading nothing.

    Discovery is opt-in. Importing this function never auto-loads
    anything.
    """
    eps = entry_points(group=_ENTRY_POINT_GROUP)
    by_name = {ep.name: ep for ep in eps}

    if names is None:
        selected = list(by_name.values())
    else:
        missing = [n for n in names if n not in by_name]
        if missing:
            raise KeyError(
                f"Unknown soniq plugin(s): {missing!r}. "
                f"Available: {sorted(by_name)!r}"
            )
        selected = [by_name[n] for n in names]

    instances: List[SoniqPlugin] = []
    for ep in selected:
        factory = ep.load()
        instances.append(factory())
    return instances
