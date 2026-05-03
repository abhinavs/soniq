"""
Extended tests for cli/colors.py — print functions and StatusIcon.
"""

import pytest

from soniq.cli.colors import (
    Colors,
    StatusIcon,
    print_header,
    print_key_value,
    print_list,
    print_progress_bar,
    print_section,
    print_status,
    print_table,
)


@pytest.fixture(autouse=True)
def _no_color(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")


class TestStatusIconMethods:
    def test_warning(self):
        assert StatusIcon.warning() == "[WARNING]"

    def test_info(self):
        assert StatusIcon.info() == "[INFO]"

    def test_loading(self):
        assert StatusIcon.loading() == "[LOADING]"

    def test_rocket(self):
        assert StatusIcon.rocket() == ">>>"

    def test_workers(self):
        assert StatusIcon.workers() == "[WORKERS]"


class TestPrintStatus:
    def test_print_status_success(self, capsys):
        print_status("All good", "success")
        captured = capsys.readouterr()
        assert "All good" in captured.out

    def test_print_status_error(self, capsys):
        print_status("Something broke", "error")
        captured = capsys.readouterr()
        assert "Something broke" in captured.out

    def test_print_status_with_prefix(self, capsys):
        print_status("msg", "info", prefix="test")
        captured = capsys.readouterr()
        assert "test" in captured.out
        assert "msg" in captured.out


class TestPrintHeader:
    def test_header(self, capsys):
        print_header("Test Header")
        captured = capsys.readouterr()
        assert "Test Header" in captured.out
        assert "=" in captured.out


class TestPrintSection:
    def test_section(self, capsys):
        print_section("Section Title")
        captured = capsys.readouterr()
        assert "Section Title" in captured.out
        assert "-" in captured.out


class TestPrintTable:
    def test_table_with_data(self, capsys):
        headers = ["Name", "Status"]
        rows = [["job-1", "done"], ["job-2", "failed"]]
        print_table(headers, rows)
        captured = capsys.readouterr()
        assert "Name" in captured.out
        assert "job-1" in captured.out
        assert "done" in captured.out

    def test_table_empty(self, capsys):
        print_table(["A", "B"], [])
        captured = capsys.readouterr()
        assert "No data" in captured.out


class TestPrintProgressBar:
    def test_progress_bar(self, capsys):
        print_progress_bar(50, 100)
        captured = capsys.readouterr()
        assert "50.0%" in captured.out

    def test_progress_bar_zero_total(self, capsys):
        print_progress_bar(0, 0)
        captured = capsys.readouterr()
        assert "100" in captured.out


class TestPrintKeyValue:
    def test_key_value(self, capsys):
        print_key_value("Status", "active")
        captured = capsys.readouterr()
        assert "Status" in captured.out
        assert "active" in captured.out

    def test_key_value_indented(self, capsys):
        print_key_value("Key", "val", indent=2)
        captured = capsys.readouterr()
        assert "Key" in captured.out


class TestPrintList:
    def test_list(self, capsys):
        print_list(["alpha", "beta", "gamma"])
        captured = capsys.readouterr()
        assert "alpha" in captured.out
        assert "beta" in captured.out


class TestStatusIconWithColor:
    def test_success_with_color(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setenv("FORCE_COLOR", "1")
        result = StatusIcon.success()
        assert Colors.GREEN in result

    def test_error_with_color(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setenv("FORCE_COLOR", "1")
        result = StatusIcon.error()
        assert Colors.RED in result


class TestPrintWithColor:
    def test_header_with_color(self, capsys, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setenv("FORCE_COLOR", "1")
        print_header("Colored")
        captured = capsys.readouterr()
        assert Colors.CYAN in captured.out

    def test_section_with_color(self, capsys, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setenv("FORCE_COLOR", "1")
        print_section("Section")
        captured = capsys.readouterr()
        assert Colors.BLUE in captured.out

    def test_table_with_color(self, capsys, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setenv("FORCE_COLOR", "1")
        print_table(["H"], [["row"]])
        captured = capsys.readouterr()
        assert Colors.BOLD in captured.out

    def test_progress_with_color(self, capsys, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setenv("FORCE_COLOR", "1")
        print_progress_bar(5, 10)
        captured = capsys.readouterr()
        assert Colors.GREEN in captured.out

    def test_key_value_with_color(self, capsys, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setenv("FORCE_COLOR", "1")
        print_key_value("K", "V")
        captured = capsys.readouterr()
        assert Colors.CYAN in captured.out

    def test_list_with_color(self, capsys, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setenv("FORCE_COLOR", "1")
        print_list(["a"])
        captured = capsys.readouterr()
        assert Colors.DIM in captured.out

    def test_status_with_color(self, capsys, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setenv("FORCE_COLOR", "1")
        print_status("msg", "success", prefix="pfx")
        captured = capsys.readouterr()
        assert Colors.GREEN in captured.out
