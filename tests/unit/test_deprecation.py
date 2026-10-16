import logging

import pytest

from soniq._deprecation import (
    SoniqDeprecationWarning,
    _logged,
    deprecated_param,
    warn_deprecated,
)

KW = dict(since="0.1", removed_in="0.3", use_instead="new_thing")


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("SONIQ_DEPRECATIONS", raising=False)
    _logged.clear()


def test_warns_with_subclass_of_deprecation_warning():
    with pytest.warns(SoniqDeprecationWarning, match="old_thing is deprecated") as rec:
        warn_deprecated("old_thing", **KW)
    assert issubclass(SoniqDeprecationWarning, DeprecationWarning)
    assert "removed in 0.3" in str(rec[0].message)
    assert rec[0].filename == __file__


def test_logs_once_at_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="soniq"):
        with pytest.warns(SoniqDeprecationWarning):
            warn_deprecated("old_thing", **KW)
            warn_deprecated("old_thing", **KW)
    records = [r for r in caplog.records if r.name == "soniq"]
    assert len(records) == 1
    assert "new_thing" in records[0].getMessage()


def test_error_mode_raises(monkeypatch):
    monkeypatch.setenv("SONIQ_DEPRECATIONS", "error")
    with pytest.raises(SoniqDeprecationWarning):
        warn_deprecated("old_thing", **KW)


def test_ignore_mode_is_silent(monkeypatch, caplog, recwarn):
    monkeypatch.setenv("SONIQ_DEPRECATIONS", "ignore")
    with caplog.at_level(logging.WARNING, logger="soniq"):
        warn_deprecated("old_thing", **KW)
    assert not recwarn.list
    assert not caplog.records


def test_deprecated_param_maps_old_to_new():
    @deprecated_param("old", use_instead="new", since="0.1", removed_in="0.3")
    def f(new=None):
        return new

    with pytest.warns(SoniqDeprecationWarning, match="'old' argument of f"):
        assert f(old=5) == 5
    assert f(new=6) == 6
    with pytest.raises(TypeError):
        f(old=1, new=2)
