"""
Tests for the feature subcommands (``dashboard``, ``scheduler``,
``dead-letter``).

After the flat-CLI rewrite (S8) each subcommand lives in its own
module exposing one ``add_X_cmd(subparsers)`` function. These tests
exercise that function via the top-level parser instead of poking at
a global registry: the parser is the contract.
"""

from __future__ import annotations

import argparse

import pytest

from soniq.cli.dashboard import add_dashboard_cmd
from soniq.cli.dead_letter import add_dead_letter_cmd
from soniq.cli.inspect import add_inspect_cmd
from soniq.cli.main import build_parser
from soniq.cli.scheduler import add_scheduler_cmd


def _bare_subparser():
    """Build a parser with a single ``subparsers`` and return both."""
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    return parser, sub


class TestAddDashboardCmd:
    def test_default_host_and_port(self):
        parser, sub = _bare_subparser()
        add_dashboard_cmd(sub)
        args = parser.parse_args(["dashboard"])
        assert args.command == "dashboard"
        assert args.host == "127.0.0.1"
        assert args.port == 6161
        assert callable(args.func)

    def test_custom_host_and_port(self):
        parser, sub = _bare_subparser()
        add_dashboard_cmd(sub)
        args = parser.parse_args(["dashboard", "--host", "0.0.0.0", "--port", "9000"])
        assert args.host == "0.0.0.0"
        assert args.port == 9000


class TestAddSchedulerCmd:
    def test_default_check_interval(self):
        parser, sub = _bare_subparser()
        add_scheduler_cmd(sub)
        args = parser.parse_args(["scheduler"])
        assert args.check_interval == 60

    def test_status_flag_removed(self):
        # --status moved to `soniq inspect` (it now reports schedule counts
        # alongside worker status). The scheduler subcommand should reject it.
        parser, sub = _bare_subparser()
        add_scheduler_cmd(sub)
        with pytest.raises(SystemExit):
            parser.parse_args(["scheduler", "--status"])


class TestAddInspectCmd:
    def test_defaults(self):
        parser, sub = _bare_subparser()
        add_inspect_cmd(sub)
        args = parser.parse_args(["inspect"])
        assert args.stale is False
        assert args.cleanup is False
        assert args.schedules is False

    def test_schedules_flag(self):
        parser, sub = _bare_subparser()
        add_inspect_cmd(sub)
        args = parser.parse_args(["inspect", "--schedules"])
        assert args.schedules is True


class TestAddDeadLetterCmd:
    def test_action_choices(self):
        parser, sub = _bare_subparser()
        add_dead_letter_cmd(sub)
        for action in ("list", "replay", "delete", "cleanup", "export"):
            args = parser.parse_args(["dead-letter", action])
            assert args.action == action

    def test_invalid_action_rejected(self):
        parser, sub = _bare_subparser()
        add_dead_letter_cmd(sub)
        with pytest.raises(SystemExit):
            parser.parse_args(["dead-letter", "nope"])

    def test_replay_collects_job_ids(self):
        parser, sub = _bare_subparser()
        add_dead_letter_cmd(sub)
        args = parser.parse_args(["dead-letter", "replay", "id-1", "id-2"])
        assert args.action == "replay"
        assert args.job_ids == ["id-1", "id-2"]

    def test_yes_and_dry_run_flags_parse(self):
        parser, sub = _bare_subparser()
        add_dead_letter_cmd(sub)
        args = parser.parse_args(
            ["dead-letter", "replay", "--all", "--yes", "--dry-run"]
        )
        assert args.all is True
        assert args.yes is True
        assert args.dry_run is True

    def test_short_yes_flag(self):
        parser, sub = _bare_subparser()
        add_dead_letter_cmd(sub)
        args = parser.parse_args(["dead-letter", "delete", "--all", "-y"])
        assert args.yes is True


class TestTopLevelParserWiresAllFeatures:
    """The full parser produced by ``build_parser`` must list every
    feature subcommand. Pinning prevents a future ``add_*_cmd`` from
    being silently dropped from ``main``."""

    def test_lists_every_feature_subcommand(self):
        parser = build_parser()
        # Parse one no-op arg per feature subcommand to assert each is wired.
        for cmd in ("dashboard", "scheduler"):
            args = parser.parse_args([cmd])
            assert args.command == cmd
            assert callable(args.func)

    def test_dead_letter_requires_action(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["dead-letter"])
