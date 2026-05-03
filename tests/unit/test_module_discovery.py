"""
Tests for module discovery: sys.path injection, multi-module parsing,
error reporting.
"""

import sys

import pytest


def test_cwd_added_to_sys_path_when_missing(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "path", [p for p in sys.path if p != str(tmp_path)])

    from soniq.discovery import _ensure_cwd_on_path

    _ensure_cwd_on_path()

    assert str(tmp_path) in sys.path
    assert sys.path[0] == str(tmp_path)


def test_cwd_not_duplicated_when_already_on_path(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "path", [str(tmp_path)] + sys.path)

    from soniq.discovery import _ensure_cwd_on_path

    _ensure_cwd_on_path()

    assert sys.path.count(str(tmp_path)) == 1


def test_parse_single_module():
    from soniq.discovery import parse_jobs_modules

    result = parse_jobs_modules("app.tasks")
    assert result == ["app.tasks"]


def test_parse_multiple_modules_with_whitespace():
    from soniq.discovery import parse_jobs_modules

    result = parse_jobs_modules("app.tasks, billing.tasks ,notifications.jobs")
    assert result == ["app.tasks", "billing.tasks", "notifications.jobs"]


def test_parse_ignores_empty_segments():
    from soniq.discovery import parse_jobs_modules

    result = parse_jobs_modules("app.tasks,,  ,billing.tasks")
    assert result == ["app.tasks", "billing.tasks"]


def test_discover_imports_all_modules(tmp_path, monkeypatch):
    pkg = tmp_path / "mypkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "tasks_a.py").write_text("MARKER_A = True")
    (pkg / "tasks_b.py").write_text("MARKER_B = True")

    monkeypatch.chdir(tmp_path)

    from soniq.discovery import discover_and_import_modules

    discover_and_import_modules(["mypkg.tasks_a", "mypkg.tasks_b"])

    import mypkg.tasks_a
    import mypkg.tasks_b

    assert mypkg.tasks_a.MARKER_A is True
    assert mypkg.tasks_b.MARKER_B is True


def test_all_failures_reported_on_bad_modules(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    from soniq.discovery import discover_and_import_modules

    with pytest.raises(SystemExit):
        discover_and_import_modules(["does.not.exist.a", "does.not.exist.b"])

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "does.not.exist.a" in combined
    assert "does.not.exist.b" in combined


def test_partial_failure_reports_only_bad_modules(tmp_path, monkeypatch, capsys):
    pkg = tmp_path / "goodpkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "tasks.py").write_text("")

    monkeypatch.chdir(tmp_path)

    from soniq.discovery import discover_and_import_modules

    with pytest.raises(SystemExit):
        discover_and_import_modules(["goodpkg.tasks", "does.not.exist"])

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "does.not.exist" in combined
    assert "goodpkg.tasks" not in combined


def test_hint_shown_for_missing_module(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    from soniq.discovery import discover_and_import_modules

    with pytest.raises(SystemExit):
        discover_and_import_modules(["totally.missing.module"])

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "PYTHONPATH" in combined or "project root" in combined


def test_no_hint_for_syntax_error_in_module(tmp_path, monkeypatch, capsys):
    pkg = tmp_path / "brokenpkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "tasks.py").write_text("def broken(: pass")

    monkeypatch.chdir(tmp_path)

    from soniq.discovery import discover_and_import_modules

    with pytest.raises(SystemExit):
        discover_and_import_modules(["brokenpkg.tasks"])

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "PYTHONPATH" not in combined
