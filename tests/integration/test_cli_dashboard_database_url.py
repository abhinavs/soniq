"""
CLI ``soniq dashboard --database-url`` honors the explicit URL.

The dashboard subcommand resolves a fresh ``Soniq`` instance from
``--database-url`` for the "Using instance-based configuration" log
line, but historically did not thread that instance into
``run_dashboard``. The result: the dashboard process silently used the
global app's database URL while the CLI claimed otherwise.

This test pins the contract: the resolved instance must reach
``run_dashboard`` (and therefore ``create_dashboard_app``) so the
dashboard actually serves data from the URL the operator specified.
"""

from __future__ import annotations

import argparse

import pytest

from soniq.cli.dashboard import handle_dashboard


@pytest.mark.asyncio
async def test_handle_dashboard_passes_resolved_instance_to_run_dashboard(
    monkeypatch,
):
    """When ``--database-url`` is given, the resolved Soniq must be
    handed to ``run_dashboard`` as ``soniq_app``."""
    captured: dict = {}

    async def _fake_run_dashboard(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return 0

    import soniq.dashboard.server as server_mod

    monkeypatch.setattr(server_mod, "run_dashboard", _fake_run_dashboard)
    monkeypatch.setattr("soniq.DASHBOARD_AVAILABLE", True, raising=False)

    explicit_url = (
        "postgresql://soniq_test:soniq_test@127.0.0.1:5432/cli_dashboard_url_test"
    )

    args = argparse.Namespace(
        host="127.0.0.1",
        port=6161,
        reload=False,
        database_url=explicit_url,
    )

    rc = await handle_dashboard(args)
    assert rc == 0

    assert "kwargs" in captured, "run_dashboard was never invoked"
    soniq_app = captured["kwargs"].get("soniq_app")
    assert soniq_app is not None, (
        "handle_dashboard did not pass the --database-url-resolved instance "
        "to run_dashboard. Without soniq_app=, create_dashboard_app falls "
        "back to the global Soniq and the dashboard uses the wrong DB."
    )
    assert soniq_app.settings.database_url == explicit_url, (
        f"Dashboard received a Soniq with DB URL "
        f"{soniq_app.settings.database_url!r}, expected {explicit_url!r}."
    )
