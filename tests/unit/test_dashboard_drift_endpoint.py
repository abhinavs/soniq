"""
Tests for the deploy-skew dashboard endpoint.

The endpoint reads the soniq_task_registry observability table and
joins against soniq_jobs to surface names with recent queued or dead-
letter rows that no worker has registered. The endpoint *reads* the
registry table; the enqueue path still does not.

The data-layer query is Postgres-specific; integration coverage
against a live DB lives in tests/integration/. Here we exercise:
- The data-layer function is importable.
- The API endpoint is registered on the FastAPI app.
- The endpoint description / handler points at the right data layer.

The fastapi import is gated; tests skip if fastapi is not installed.
"""

from __future__ import annotations

import pytest


def test_drift_data_layer_method_importable():
    """`DashboardService.get_task_registry_drift` must import without
    requiring a live database."""
    from soniq.dashboard.app import DashboardService

    assert callable(DashboardService.get_task_registry_drift)


def test_drift_endpoint_registered_on_server():
    """The /api/tasks/drift route is wired on the FastAPI app."""
    fastapi = pytest.importorskip("fastapi")  # noqa: F841

    from soniq import Soniq
    from soniq.dashboard.server import create_dashboard_app

    soniq_app = Soniq(backend="memory")
    app = create_dashboard_app(soniq_app)
    routes = {getattr(r, "path", None) for r in app.routes}
    assert "/api/tasks/drift" in routes


def test_drift_endpoint_accepts_window_minutes_query_param():
    """The method takes window_minutes; the default is 60."""
    fastapi = pytest.importorskip("fastapi")  # noqa: F841
    import inspect

    from soniq.dashboard.app import DashboardService

    sig = inspect.signature(DashboardService.get_task_registry_drift)
    assert "window_minutes" in sig.parameters
    assert sig.parameters["window_minutes"].default == 60
