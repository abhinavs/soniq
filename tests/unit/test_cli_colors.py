"""
Tests for cli/colors.py — color utilities and StatusIcon.
"""

from soniq.cli.colors import (
    Colors,
    StatusIcon,
    bold,
    colorize,
    dim,
    error,
    highlight,
    info,
    success,
    supports_color,
    warning,
)


class TestSupportsColor:
    def test_no_color_env_disables(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        assert supports_color() is False

    def test_force_color_env_enables(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setenv("FORCE_COLOR", "1")
        assert supports_color() is True


class TestColorize:
    def test_colorize_with_force_color(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setenv("FORCE_COLOR", "1")
        result = colorize("hello", Colors.GREEN)
        assert Colors.GREEN in result
        assert "hello" in result
        assert Colors.RESET in result

    def test_colorize_bold(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setenv("FORCE_COLOR", "1")
        result = colorize("hello", Colors.RED, bold=True)
        assert Colors.BOLD in result

    def test_colorize_no_color(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        result = colorize("hello", Colors.GREEN)
        assert result == "hello"


class TestColorHelpers:
    def test_success(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        assert success("ok") == "ok"

    def test_error(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        assert error("fail") == "fail"

    def test_warning(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        assert warning("warn") == "warn"

    def test_info(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        assert info("note") == "note"

    def test_highlight(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        assert highlight("x") == "x"

    def test_dim(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        assert dim("x") == "x"

    def test_bold(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        assert bold("x") == "x"

    def test_success_with_color(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setenv("FORCE_COLOR", "1")
        result = success("ok")
        assert Colors.GREEN in result

    def test_dim_with_color(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setenv("FORCE_COLOR", "1")
        result = dim("faint")
        assert Colors.DIM in result

    def test_bold_with_color(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setenv("FORCE_COLOR", "1")
        result = bold("strong")
        assert Colors.BOLD in result


class TestStatusIcon:
    def test_status_icon_methods(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        assert StatusIcon.success() == "[OK]"
        assert (
            StatusIcon.error() == "[FAIL]"
            or "FAIL" in StatusIcon.error()
            or "ERR" in StatusIcon.error()
        )


class TestColorsConstants:
    def test_ansi_codes(self):
        assert "\033[" in Colors.RED
        assert "\033[" in Colors.GREEN
        assert "\033[0m" == Colors.RESET
