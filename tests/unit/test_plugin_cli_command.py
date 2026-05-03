"""Tests for the plugin CLI extension point.

Two layers:

1. ``app.cli.add_command(spec)`` records a CommandSpec on the app.
2. ``soniq.cli.main.build_parser(plugin_app=app)`` folds those specs
   into the argparse subparsers alongside the built-ins, and the
   handler is reachable via ``parser.parse_args``.

The tests build a parser directly instead of going through entry-point
discovery so they don't depend on ``pip install``-time state - the
``main()`` integration with entry points is covered by
``test_example_plugin_installs.py`` in the integration suite.
"""

from __future__ import annotations

from soniq import Soniq
from soniq.cli.main import build_parser
from soniq.plugin import CommandSpec


def _make_app_with_command(name: str, **arg_kwargs):
    """Build a Soniq instance with one plugin-registered CLI command."""
    captured = {}

    def handler(args):
        captured["args"] = args
        return 0

    app = Soniq(backend="memory")
    app.cli.add_command(
        CommandSpec(
            name=name,
            help=f"{name} subcommand",
            handler=handler,
            arguments=[arg_kwargs] if arg_kwargs else [],
        )
    )
    return app, handler, captured


def test_add_command_records_spec_on_app():
    app, _handler, _ = _make_app_with_command("plugin-cmd")
    specs = app.cli._commands
    assert len(specs) == 1
    assert specs[0].name == "plugin-cmd"


def test_build_parser_folds_plugin_command_into_subparsers():
    app, handler, captured = _make_app_with_command(
        "plugin-cmd",
        args=["--flag"],
        kwargs={"action": "store_true"},
    )
    parser = build_parser(plugin_app=app)
    args = parser.parse_args(["plugin-cmd", "--flag"])
    assert args.command == "plugin-cmd"
    assert args.flag is True
    # Dispatch via the recorded func to prove the handler is wired.
    assert args.func is handler
    args.func(args)
    assert captured["args"] is args


def test_plugin_command_appears_alongside_builtins():
    """The flat-CLI list of subcommands must still include the
    built-ins after a plugin adds one - we don't replace, we extend."""
    app, _, _ = _make_app_with_command("plugin-cmd")
    parser = build_parser(plugin_app=app)
    import argparse

    sub_action = next(
        a for a in parser._actions if isinstance(a, argparse._SubParsersAction)
    )
    choices = set(sub_action.choices.keys())
    assert "plugin-cmd" in choices
    assert {"worker", "setup", "status", "inspect"} <= choices


def test_command_without_plugin_app_skips_plugin_specs():
    """``build_parser`` with no plugin_app must not pretend plugin
    commands exist - tests / scripts that introspect the parser
    without plugins should see only the built-ins."""
    parser = build_parser()
    import argparse

    sub_action = next(
        a for a in parser._actions if isinstance(a, argparse._SubParsersAction)
    )
    assert "plugin-cmd" not in sub_action.choices
