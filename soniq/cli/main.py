"""
Soniq CLI - flat dispatch.

Each subcommand lives in its own module and exposes one
``add_X_cmd(subparsers)`` function that creates the subparser and
attaches its handler via ``parser.set_defaults(func=...)``. ``main``
just lists them and dispatches whichever the user picked.

Plugin commands. Plugins register CLI specs in their ``install()``
via ``app.cli.add_command(spec)``. Because the CLI starts before any
``Soniq`` instance exists in the user's program, the parser builder
itself constructs a bootstrap ``Soniq``, loads plugins from the
``soniq.plugins`` entry-point group when the operator opted in via
``--plugins=...`` / ``SONIQ_PLUGINS=...``, then folds the registered
``CommandSpec`` instances into the argparse subparsers alongside the
built-ins. Discovery is opt-in - the parser does no entry-point work
unless the operator asked for it.
"""

import argparse
import asyncio
import os
import sys
from typing import List, Optional

from soniq import Soniq
from soniq.plugin import discover_plugins

from .colors import print_status
from .dashboard import add_dashboard_cmd
from .dead_letter import add_dead_letter_cmd
from .inspect import add_inspect_cmd
from .migrate_status import add_migrate_status_cmd
from .scheduler import add_scheduler_cmd
from .setup import add_setup_cmd
from .status import add_status_cmd
from .tasks import add_tasks_cmd
from .worker import add_worker_cmd


def build_parser(plugin_app: Optional[object] = None) -> argparse.ArgumentParser:
    """Construct the top-level parser with every subcommand wired in.

    When ``plugin_app`` is provided, plugin-registered ``CommandSpec``
    instances are folded into the parser after the built-ins. Tests
    pass a pre-configured ``Soniq`` here to assert that
    ``app.cli.add_command`` plumbs through. ``main`` resolves the app
    from CLI flags / env and reuses this entry point.
    """
    parser = argparse.ArgumentParser(
        description="Soniq CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  soniq worker --concurrency 4 --queues default,urgent
  soniq setup
  soniq status --verbose --jobs

For more information, visit: https://github.com/abhinavs/soniq
        """,
    )
    # Surface the plugin discovery flag at the top level so the parser
    # itself accepts it; ``parse_known_args`` reads it before we know
    # which subcommand the operator wants.
    parser.add_argument(
        "--plugins",
        default=None,
        metavar="NAMES",
        help=(
            "Comma-separated soniq plugin names to load via the "
            "'soniq.plugins' entry-point group. Same as SONIQ_PLUGINS."
        ),
    )

    sub = parser.add_subparsers(dest="command", title="Commands")

    add_worker_cmd(sub)
    add_setup_cmd(sub)
    add_status_cmd(sub)
    add_inspect_cmd(sub)
    add_migrate_status_cmd(sub)
    add_dashboard_cmd(sub)
    add_scheduler_cmd(sub)
    add_dead_letter_cmd(sub)
    add_tasks_cmd(sub)

    if plugin_app is not None:
        _register_plugin_commands(sub, plugin_app)

    return parser


def _resolve_plugin_names(argv: List[str]) -> Optional[List[str]]:
    """Read ``--plugins`` / ``SONIQ_PLUGINS`` without consuming argv.

    The CLI needs to know which plugins to load *before* ``parse_args``
    runs (plugins may register subcommands). This pre-parse uses a tiny
    side parser with ``parse_known_args`` so we don't accidentally
    error on subcommand-specific flags the main parser hasn't seen yet.
    """
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--plugins", default=None)
    ns, _ = pre.parse_known_args(argv)
    raw = ns.plugins or os.environ.get("SONIQ_PLUGINS")
    if not raw:
        return None
    names = [n.strip() for n in raw.split(",") if n.strip()]
    return names or None


def _build_plugin_app(plugin_names: List[str]) -> object:
    """Build a bootstrap ``Soniq`` and install the named plugins.

    The app is *not* initialized (no DB connection); plugins only run
    their synchronous ``install()`` so they can register CLI specs.
    Deferred work (``on_startup``) waits for ``soniq setup``.
    """
    app = Soniq()
    for plugin in discover_plugins(plugin_names):
        app.use(plugin)
    return app


def _register_plugin_commands(subparsers, plugin_app: object) -> None:
    """Fold CommandSpecs registered by plugins into the parser."""
    cli = getattr(plugin_app, "cli", None)
    if cli is None:
        return
    for spec in cli._commands:
        sub_parser = subparsers.add_parser(
            spec.name,
            help=spec.help,
            description=spec.description or spec.help,
        )
        for arg in spec.arguments:
            args = arg.get("args", [])
            kwargs = arg.get("kwargs", {})
            sub_parser.add_argument(*args, **kwargs)
        sub_parser.set_defaults(func=spec.handler)


def main(argv: Optional[List[str]] = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    plugin_names = _resolve_plugin_names(argv)
    plugin_app = None
    if plugin_names is not None:
        try:
            plugin_app = _build_plugin_app(plugin_names)
        except Exception as e:
            print_status(f"Plugin discovery failed: {e}", "error")
            return 1

    parser = build_parser(plugin_app=plugin_app)
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 1

    try:
        if hasattr(args, "func"):
            if asyncio.iscoroutinefunction(args.func):
                rc = asyncio.run(args.func(args))
            else:
                rc = args.func(args)
            return int(rc) if rc is not None else 0
        print_status(f"Command '{args.command}' has no handler", "error")
        return 1
    except KeyboardInterrupt:
        print_status("Operation interrupted by user", "info")
        return 0
    except Exception as e:
        print_status(f"Command failed: {e}", "error")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
