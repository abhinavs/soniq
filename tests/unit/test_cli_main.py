"""
Tests for cli/main.py — CLI entry point.
"""

from unittest.mock import patch

from soniq.cli.main import main


class TestMain:
    def test_no_command_prints_help(self):
        with patch("sys.argv", ["soniq"]):
            result = main()
            assert result == 1  # No command → help + exit 1

    def test_unknown_command_returns_error(self):
        with patch("sys.argv", ["soniq", "nonexistent_command_xyz"]):
            # argparse may raise SystemExit for unknown commands
            try:
                result = main()
                assert result == 1
            except SystemExit as e:
                assert e.code != 0
