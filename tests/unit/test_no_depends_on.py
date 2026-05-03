"""
Verify that depends_on / job dependencies feature has been removed.

It was experimental and unimplemented in the worker - shipping
a feature that doesn't work erodes trust.
"""

import importlib

import pytest


def test_no_dependencies_module():
    """soniq.features.dependencies should not exist."""
    with pytest.raises(ImportError):
        importlib.import_module("soniq.features.dependencies")


def test_no_dependencies_in_features_init():
    """dependencies should not be exported from soniq.features."""
    import soniq.features as feat

    assert "dependencies" not in feat.__all__


def test_no_dependencies_enabled_setting():
    """dependencies_enabled should not be a setting."""
    from soniq.settings import SoniqSettings

    assert (
        not hasattr(SoniqSettings.model_fields, "dependencies_enabled")
        or "dependencies_enabled" not in SoniqSettings.model_fields
    )


def test_soniq_has_no_depends_on_attribute():
    """The package must not expose a depends_on symbol at the top level."""
    import soniq

    assert not hasattr(soniq, "depends_on")


def test_from_soniq_import_depends_on_raises():
    """Direct import of depends_on must fail with ImportError."""
    with pytest.raises(ImportError):
        exec("from soniq import depends_on", {"__name__": "_probe"})


def test_depends_on_not_in_soniq_all():
    """depends_on must not appear in soniq.__all__."""
    import soniq

    assert "depends_on" not in soniq.__all__
