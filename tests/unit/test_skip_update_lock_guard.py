"""
Tests that SONIQ_SKIP_UPDATE_LOCK is only honored in debug/test mode.
"""

from soniq.backends.postgres import PostgresBackend


def test_skip_lock_ignored_in_production(monkeypatch):
    """SONIQ_SKIP_UPDATE_LOCK must be ignored when environment=production and debug=False."""
    monkeypatch.setenv("SONIQ_SKIP_UPDATE_LOCK", "true")

    from soniq.settings import get_settings

    settings = get_settings(reload=True)
    monkeypatch.setattr(settings, "debug", False)
    monkeypatch.setattr(settings, "environment", "production")

    assert PostgresBackend._should_skip_lock() is False


def test_skip_lock_honored_in_debug(monkeypatch):
    """SONIQ_SKIP_UPDATE_LOCK must be honored when debug=True."""
    monkeypatch.setenv("SONIQ_SKIP_UPDATE_LOCK", "true")

    from soniq.settings import get_settings

    settings = get_settings(reload=True)
    monkeypatch.setattr(settings, "debug", True)
    monkeypatch.setattr(settings, "environment", "production")

    assert PostgresBackend._should_skip_lock() is True


def test_skip_lock_honored_in_testing(monkeypatch):
    """SONIQ_SKIP_UPDATE_LOCK must be honored when environment=testing."""
    monkeypatch.setenv("SONIQ_SKIP_UPDATE_LOCK", "true")

    from soniq.settings import get_settings

    settings = get_settings(reload=True)
    monkeypatch.setattr(settings, "debug", False)
    monkeypatch.setattr(settings, "environment", "testing")

    assert PostgresBackend._should_skip_lock() is True


def test_skip_lock_false_when_env_not_set(monkeypatch):
    """When SONIQ_SKIP_UPDATE_LOCK is not set, lock is always active."""
    monkeypatch.delenv("SONIQ_SKIP_UPDATE_LOCK", raising=False)

    assert PostgresBackend._should_skip_lock() is False
