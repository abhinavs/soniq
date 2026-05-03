"""
Tests for API consistency on the Soniq instance.
"""

import inspect

from soniq.app import Soniq


def test_list_jobs_has_limit_param():
    """
    list_jobs on the Soniq instance must have a 'limit' parameter with a default.
    """
    sig_instance = inspect.signature(Soniq.list_jobs)
    assert "limit" in sig_instance.parameters
    assert sig_instance.parameters["limit"].default is not inspect.Parameter.empty


def test_no_features_umbrella():
    """The SoniqFeatures umbrella (and the `features` singleton) is gone.

    Each feature service is now reached via a lazy property on the
    ``Soniq`` instance: ``app.webhooks``, ``app.dead_letter``,
    ``app.scheduler``, ``app.signing``, ``app.logs``,
    ``app.dashboard_data``.
    """
    import soniq.features as feat_pkg

    assert not hasattr(feat_pkg, "SoniqFeatures")
    assert not hasattr(feat_pkg, "EnterpriseFeatures")
    assert not hasattr(feat_pkg, "features")
    assert not hasattr(feat_pkg, "enterprise")
    assert "SoniqFeatures" not in feat_pkg.__all__
    assert "features" not in feat_pkg.__all__
