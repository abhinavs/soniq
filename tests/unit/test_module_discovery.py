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


# --- find_soniq_app: the CLI must run jobs on the instance they registered on --


def _write_pkg(tmp_path, name, files):
    pkg = tmp_path / name
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    for filename, content in files.items():
        (pkg / filename).write_text(content)


def test_find_app_instance_in_listed_module(tmp_path, monkeypatch):
    """The instance declared at the top of the listed module is returned."""
    _write_pkg(
        tmp_path,
        "app_direct",
        {
            "jobs.py": (
                "from soniq import Soniq\n"
                "app = Soniq()\n"
                "@app.job(name='direct_job')\n"
                "async def direct_job():\n"
                "    return 'ok'\n"
            )
        },
    )
    monkeypatch.chdir(tmp_path)

    from soniq.discovery import discover_and_import_modules, find_soniq_app

    discover_and_import_modules(["app_direct.jobs"])
    app = find_soniq_app(["app_direct.jobs"])

    assert app is not None
    assert app.registry.get_job("direct_job") is not None


def test_find_app_instance_in_sibling_module(tmp_path, monkeypatch):
    """The common layout: the listed module only *imports* handlers, and the
    Soniq instance lives in a sibling module of the same package."""
    _write_pkg(
        tmp_path,
        "app_sibling",
        {
            "core.py": "from soniq import Soniq\napp = Soniq()\n",
            "handlers.py": (
                "from app_sibling.core import app\n"
                "@app.job(name='sibling_job')\n"
                "async def sibling_job():\n"
                "    return 'ok'\n"
            ),
            "jobs.py": "from app_sibling import handlers  # noqa: F401\n",
        },
    )
    monkeypatch.chdir(tmp_path)

    from soniq.discovery import discover_and_import_modules, find_soniq_app

    discover_and_import_modules(["app_sibling.jobs"])
    app = find_soniq_app(["app_sibling.jobs"])

    assert app is not None
    assert app.registry.get_job("sibling_job") is not None


def test_find_app_returns_none_when_no_instance(tmp_path, monkeypatch):
    _write_pkg(tmp_path, "app_none", {"jobs.py": "VALUE = 1\n"})
    monkeypatch.chdir(tmp_path)

    from soniq.discovery import discover_and_import_modules, find_soniq_app

    discover_and_import_modules(["app_none.jobs"])
    assert find_soniq_app(["app_none.jobs"]) is None


def test_find_app_prefers_instance_with_registered_jobs(tmp_path, monkeypatch):
    """A second, job-less Soniq() in the package (a dashboard sub-app, a
    fixture) must not make resolution ambiguous - the instance that actually
    owns jobs wins."""
    _write_pkg(
        tmp_path,
        "app_two_one_job",
        {
            "jobs.py": (
                "from soniq import Soniq\n"
                "dashboard = Soniq()  # no jobs registered on this one\n"
                "app = Soniq()\n"
                "@app.job(name='real_job')\n"
                "async def real_job():\n"
                "    return 'ok'\n"
            )
        },
    )
    monkeypatch.chdir(tmp_path)

    from soniq.discovery import discover_and_import_modules, find_soniq_app

    discover_and_import_modules(["app_two_one_job.jobs"])
    app = find_soniq_app(["app_two_one_job.jobs"])

    assert app is not None
    assert app.registry.get_job("real_job") is not None


def test_find_app_raises_when_multiple_instances_own_jobs(tmp_path, monkeypatch):
    """The tie-break only helps when exactly one instance has jobs. If two
    both own jobs, resolution is genuinely ambiguous and must still raise."""
    _write_pkg(
        tmp_path,
        "app_two_both_jobs",
        {
            "jobs.py": (
                "from soniq import Soniq\n"
                "app_one = Soniq()\n"
                "app_two = Soniq()\n"
                "@app_one.job(name='job_one')\n"
                "async def job_one():\n"
                "    return 'ok'\n"
                "@app_two.job(name='job_two')\n"
                "async def job_two():\n"
                "    return 'ok'\n"
            )
        },
    )
    monkeypatch.chdir(tmp_path)

    from soniq.discovery import (
        AmbiguousAppError,
        discover_and_import_modules,
        find_soniq_app,
    )

    discover_and_import_modules(["app_two_both_jobs.jobs"])
    with pytest.raises(AmbiguousAppError):
        find_soniq_app(["app_two_both_jobs.jobs"])


def test_find_app_raises_on_multiple_instances(tmp_path, monkeypatch):
    _write_pkg(
        tmp_path,
        "app_ambiguous",
        {
            "jobs.py": (
                "from soniq import Soniq\n" "app_one = Soniq()\n" "app_two = Soniq()\n"
            )
        },
    )
    monkeypatch.chdir(tmp_path)

    from soniq.discovery import (
        AmbiguousAppError,
        discover_and_import_modules,
        find_soniq_app,
    )

    discover_and_import_modules(["app_ambiguous.jobs"])
    with pytest.raises(AmbiguousAppError):
        find_soniq_app(["app_ambiguous.jobs"])
