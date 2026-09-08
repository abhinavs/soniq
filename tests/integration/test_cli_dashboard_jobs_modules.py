"""Regression: ``soniq dashboard`` must run on the job-module instance.

Same root cause as the ``dead-letter replay`` C1 bug. ``handle_dashboard``
resolved its ``Soniq`` via ``cli_app()`` (empty registry), so the
dashboard's ``/api/dead-letter/{id}/replay`` endpoint could never resolve
the target job's retry limits and returned a generic 400. It now imports
``--jobs-modules`` / ``SONIQ_JOBS_MODULES`` and routes through
``execution_app`` like ``worker``/``scheduler``.
"""

from __future__ import annotations

import argparse

import pytest

from soniq.cli.dashboard import handle_dashboard

JOBS_MODULE = "tests.fixtures.cli_jobs"  # defines `app = Soniq()` + cli_fixture_job


@pytest.mark.asyncio
async def test_handle_dashboard_uses_job_module_instance(monkeypatch):
    captured: dict = {}

    async def _fake_run_dashboard(*args, **kwargs):
        captured["kwargs"] = kwargs
        return 0

    import soniq.dashboard.server as server_mod

    monkeypatch.setattr(server_mod, "run_dashboard", _fake_run_dashboard)
    monkeypatch.setattr("soniq.DASHBOARD_AVAILABLE", True, raising=False)

    args = argparse.Namespace(
        host="127.0.0.1",
        port=6161,
        reload=False,
        database_url=None,
        jobs_modules=JOBS_MODULE,
    )

    rc = await handle_dashboard(args)
    assert rc == 0

    import tests.fixtures.cli_jobs as jobs_mod

    soniq_app = captured["kwargs"].get("soniq_app")
    assert soniq_app is jobs_mod.app, (
        "handle_dashboard built a fresh Soniq instead of the job-module "
        "instance; dead-letter replay on the dashboard would have an empty "
        "registry and always 400."
    )
