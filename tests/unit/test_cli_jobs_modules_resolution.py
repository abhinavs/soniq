"""
Test that CLI job-module resolution reads SONIQ_JOBS_MODULES fresh and in
isolation from the rest of the SONIQ_* surface.
"""

from types import SimpleNamespace

from soniq.cli._helpers import resolve_jobs_modules
from soniq.settings import configure, get_settings


def _args(jobs_modules=None):
    return SimpleNamespace(jobs_modules=jobs_modules)


def test_env_change_after_settings_cached_is_picked_up(monkeypatch):
    """A later SONIQ_JOBS_MODULES must win over the cached settings object."""
    monkeypatch.setenv("SONIQ_JOBS_MODULES", "app.tasks")
    assert resolve_jobs_modules(_args()) == ["app.tasks"]

    get_settings()  # prime the global settings cache
    monkeypatch.setenv("SONIQ_JOBS_MODULES", "billing.tasks")
    assert resolve_jobs_modules(_args()) == ["billing.tasks"]


def test_unrelated_invalid_setting_does_not_break_resolution(monkeypatch):
    """A bad value on another SONIQ_* field must not fail module discovery."""
    monkeypatch.setenv("SONIQ_JOBS_MODULES", "app.tasks")
    monkeypatch.setenv("SONIQ_CONCURRENCY", "not-a-number")
    monkeypatch.setenv("SONIQ_MAX_RETRIES", "also-not-a-number")

    assert resolve_jobs_modules(_args()) == ["app.tasks"]


def test_dotenv_value_is_seen(monkeypatch, tmp_path):
    """SONIQ_JOBS_MODULES set only in .env (never exported) must be honoured."""
    monkeypatch.delenv("SONIQ_JOBS_MODULES", raising=False)
    (tmp_path / ".env").write_text("SONIQ_JOBS_MODULES=dotenv.tasks\n")
    monkeypatch.chdir(tmp_path)

    assert resolve_jobs_modules(_args()) == ["dotenv.tasks"]


def test_process_env_wins_over_dotenv(monkeypatch, tmp_path):
    """An exported var still takes precedence over the .env file."""
    (tmp_path / ".env").write_text("SONIQ_JOBS_MODULES=dotenv.tasks\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SONIQ_JOBS_MODULES", "exported.tasks")

    assert resolve_jobs_modules(_args()) == ["exported.tasks"]


def test_flag_appends_after_env_and_dedupes(monkeypatch):
    """--jobs-modules adds to the env base, first occurrence wins."""
    monkeypatch.setenv("SONIQ_JOBS_MODULES", "app.tasks")

    resolved = resolve_jobs_modules(_args("billing.tasks,app.tasks"))

    assert resolved == ["app.tasks", "billing.tasks"]


def test_unset_env_and_no_flag_resolves_empty(monkeypatch, tmp_path):
    """Nothing configured anywhere resolves to an empty list, not an error."""
    monkeypatch.delenv("SONIQ_JOBS_MODULES", raising=False)
    monkeypatch.chdir(tmp_path)

    assert resolve_jobs_modules(_args()) == []


def test_resolution_does_not_clobber_programmatic_settings(monkeypatch):
    """Resolving modules must leave a programmatic configure() untouched."""
    monkeypatch.setenv("SONIQ_JOBS_MODULES", "app.tasks")
    configure(jobs_modules="programmatic.tasks", concurrency=9)

    resolve_jobs_modules(_args())

    settings = get_settings()
    assert settings.jobs_modules == "programmatic.tasks"
    assert settings.concurrency == 9
