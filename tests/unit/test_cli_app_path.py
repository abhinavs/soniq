"""Unit tests for the ``--app`` flag and the ``APP`` positional."""

import sys
import types
from types import SimpleNamespace

import pytest

from soniq import Soniq
from soniq.cli._helpers import load_app, resolve_jobs_modules
from soniq.cli.main import build_parser

INSTANCE_URL = "postgresql://localhost/from_instance"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    monkeypatch.delenv("SONIQ_DATABASE_URL", raising=False)
    monkeypatch.delenv("SONIQ_JOBS_MODULES", raising=False)
    monkeypatch.chdir(tmp_path)  # no stray .env


@pytest.fixture
def fake_module(monkeypatch):
    """Register an in-memory module ``fake_app_mod`` holding a Soniq instance."""
    module = types.ModuleType("fake_app_mod")
    module.soniq = Soniq(database_url=INSTANCE_URL)
    module.not_an_app = "hello"
    monkeypatch.setitem(sys.modules, "fake_app_mod", module)
    return module


def _args(app, database_url=None, jobs_modules=None):
    return SimpleNamespace(
        app=app, database_url=database_url, jobs_modules=jobs_modules
    )


# Parsing


POSITIONAL_APP_COMMANDS = [
    "worker",
    "setup",
    "scheduler",
    "status",
    "inspect",
    "dashboard",
    "migrate-status",
]


@pytest.mark.parametrize("command", POSITIONAL_APP_COMMANDS)
def test_positional_app_is_parsed(command, fake_module):
    args = build_parser().parse_args([command, "fake_app_mod:soniq"])
    assert args.app == "fake_app_mod:soniq"


def test_positional_after_flags(fake_module):
    args = build_parser().parse_args(
        ["worker", "--concurrency", "2", "fake_app_mod:soniq"]
    )
    assert args.app == "fake_app_mod:soniq"
    assert args.concurrency == 2


def test_no_app_leaves_app_none():
    assert build_parser().parse_args(["worker"]).app is None


@pytest.mark.parametrize("command", POSITIONAL_APP_COMMANDS)
def test_non_app_positional_is_still_unrecognized(command, capsys):
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args([command, "no_such_module_xyz"])
    assert exc.value.code == 2
    assert "unrecognized arguments: no_such_module_xyz" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    [
        ["status", "--app", "m:a"],
        ["inspect", "--app", "m:a"],
        ["dashboard", "--app", "m:a"],
        ["migrate-status", "--app", "m:a"],
        ["dead-letter", "list", "--app", "m:a"],
    ],
)
def test_app_flag_on_other_commands(argv):
    assert build_parser().parse_args(argv).app == "m:a"


def test_dead_letter_positionals_are_not_read_as_app():
    args = build_parser().parse_args(["dead-letter", "replay", "a:b", "c"])
    assert args.app is None
    assert args.job_ids == ["a:b", "c"]


def test_app_given_twice_conflicts(fake_module):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["worker", "fake_app_mod:soniq", "--app", "x:y"])


# Loading


def test_load_module_attribute(fake_module):
    assert load_app(_args("fake_app_mod:soniq")) is fake_module.soniq


def test_load_module_only_finds_instance(fake_module):
    assert load_app(_args("fake_app_mod")) is fake_module.soniq


def test_no_app_returns_none():
    assert load_app(_args(None)) is None


@pytest.mark.parametrize(
    "path, message",
    [
        ("no_such_module_xyz:soniq", "module 'no_such_module_xyz' not found"),
        ("fake_app_mod:missing", "has no attribute 'missing'"),
        ("fake_app_mod:not_an_app", "is a str, not a Soniq instance"),
    ],
)
def test_load_errors(fake_module, path, message):
    with pytest.raises(SystemExit) as exc:
        load_app(_args(path))
    assert message in str(exc.value.code)


# Database URL precedence


def test_instance_url_used_when_nothing_overrides(fake_module):
    assert load_app(_args("fake_app_mod:soniq")).settings.database_url == INSTANCE_URL


def test_env_var_beats_instance_url(fake_module, monkeypatch):
    monkeypatch.setenv("SONIQ_DATABASE_URL", "postgresql://localhost/from_env")
    app = load_app(_args("fake_app_mod:soniq"))
    assert app.settings.database_url == "postgresql://localhost/from_env"


def test_flag_beats_env_var(fake_module, monkeypatch):
    monkeypatch.setenv("SONIQ_DATABASE_URL", "postgresql://localhost/from_env")
    app = load_app(_args("fake_app_mod:soniq", "postgresql://localhost/from_flag"))
    assert app.settings.database_url == "postgresql://localhost/from_flag"


def test_override_refused_when_backend_is_fixed(monkeypatch):
    module = types.ModuleType("fake_mem_mod")
    module.soniq = Soniq(backend="memory")
    monkeypatch.setitem(sys.modules, "fake_mem_mod", module)
    with pytest.raises(SystemExit) as exc:
        load_app(_args("fake_mem_mod:soniq", "postgresql://localhost/other"))
    assert "already has a backend" in str(exc.value.code)


# Job modules


def test_app_module_is_imported_first(monkeypatch):
    monkeypatch.setenv("SONIQ_JOBS_MODULES", "billing.jobs")
    modules = resolve_jobs_modules(_args("myapp.jobs:soniq", jobs_modules="extra.jobs"))
    assert modules == ["myapp.jobs", "billing.jobs", "extra.jobs"]
