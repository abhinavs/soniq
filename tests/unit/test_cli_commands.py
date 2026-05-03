"""
Tests that the top-level CLI parser wires every subcommand.

After the flat-CLI rewrite (S8) there is no global registry; each
``add_X_cmd(subparsers)`` registers exactly one subcommand. The
contract is the parser: if a subcommand is missing from
``build_parser``, the tests below fail.
"""

from __future__ import annotations

import pytest

from soniq.cli.main import build_parser

EXPECTED_SUBCOMMANDS = {
    "worker",
    "setup",
    "status",
    "inspect",
    "migrate-status",
    "dashboard",
    "scheduler",
    "dead-letter",
    "tasks-check",
}


def _registered_subcommands(parser) -> set[str]:
    import argparse

    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return set(action.choices.keys())
    raise AssertionError("no subparsers on parser")


def test_every_subcommand_is_registered():
    parser = build_parser()
    assert _registered_subcommands(parser) == EXPECTED_SUBCOMMANDS


@pytest.mark.parametrize("name", sorted(EXPECTED_SUBCOMMANDS))
def test_subcommand_attaches_handler(name):
    """Every subcommand must call ``set_defaults(func=...)`` so ``main``
    can dispatch to it. ``dead-letter`` requires a positional ``action``,
    so we feed it a valid one."""
    parser = build_parser()
    extra: list[str] = []
    if name == "dead-letter":
        extra = ["list"]
    args = parser.parse_args([name, *extra])
    assert args.command == name
    assert callable(args.func)
