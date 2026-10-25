"""
Dashboard wiring contract on the in-memory backend.

The full data-layer queries are Postgres-only (raw SQL against
``soniq_jobs`` / ``soniq_dead_letter_jobs``); those are exercised by the
Postgres-gated contract suite. This file pins what does **not** need a
live database:

- ``create_dashboard_app(soniq_app)`` returns a FastAPI app with the
  canonical route set.
- The HTML home page renders.
- The plugin-panels endpoints (``/api/panels``, ``/api/panels/{id}``)
  read off ``app.dashboard._panels`` and never touch SQL, so they
  exercise on the memory backend.

The replay flow (``DashboardService.replay_dead_letter`` ->
``DeadLetterService.replay``) is also SQL-bound and lives in
``tests/contract/test_dashboard_dlq.py`` under the PG gate.
"""

from __future__ import annotations

import pytest

from soniq import Soniq
from soniq.dashboard.server import FASTAPI_AVAILABLE, create_dashboard_app

pytestmark = pytest.mark.skipif(not FASTAPI_AVAILABLE, reason="FastAPI not installed")


_EXPECTED_ROUTES = {
    "/",
    "/api/stats",
    "/api/jobs",
    "/api/queues",
    "/api/metrics",
    "/api/dead-letter/{dead_letter_id}/replay",
    "/api/jobs/{job_id}",
    "/api/jobs/{job_id}/cancel",
    "/api/workers/stats",
    "/api/jobs/timeline",
    "/api/jobs/types",
    "/api/jobs/search",
    "/api/system/health",
    "/api/tasks/drift",
    "/api/panels",
    "/api/panels/{panel_id}",
    "/api/dead-letter",
    "/api/workers",
}


@pytest.fixture
async def app():
    a = Soniq(backend="memory")
    await a.ensure_initialized()
    try:
        yield a
    finally:
        await a.close()


def test_create_dashboard_app_registers_canonical_routes(app):
    """Every documented dashboard endpoint must be wired on the FastAPI app."""
    fastapi_app = create_dashboard_app(app)
    paths = {getattr(r, "path", None) for r in fastapi_app.routes}
    missing = _EXPECTED_ROUTES - paths
    assert not missing, f"dashboard route contract drifted; missing: {missing}"


@pytest.mark.asyncio
async def test_dashboard_html_root_renders(app):
    httpx = pytest.importorskip("httpx")

    fastapi_app = create_dashboard_app(app)
    transport = httpx.ASGITransport(app=fastapi_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/html")


@pytest.mark.asyncio
async def test_panels_endpoints_work_without_sql(app):
    """``/api/panels`` and ``/api/panels/{id}`` read from
    ``app.dashboard._panels`` and never touch the storage backend."""
    httpx = pytest.importorskip("httpx")

    fastapi_app = create_dashboard_app(app)
    transport = httpx.ASGITransport(app=fastapi_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/panels")
        assert resp.status_code == 200
        assert resp.json() == []

        resp = await client.get("/api/panels/does-not-exist")
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_api_key_lets_html_page_and_preflight_through_under_a_prefix(
    app, monkeypatch
):
    httpx = pytest.importorskip("httpx")
    monkeypatch.setenv("SONIQ_DASHBOARD_API_KEY", "secret")

    fastapi_app = create_dashboard_app(app)
    transport = httpx.ASGITransport(app=fastapi_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/")).status_code == 200
        assert (await client.get("/api/panels")).status_code == 401
        assert (
            await client.get("/api/panels", headers={"X-API-Key": "secret"})
        ).status_code == 200
        assert (await client.options("/api/panels")).status_code != 401

    mounted = httpx.ASGITransport(app=fastapi_app, root_path="/admin")
    async with httpx.AsyncClient(transport=mounted, base_url="http://test") as client:
        assert (await client.get("/admin/")).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/api/jobs?limit=-1",
        "/api/jobs/search?offset=-5",
        "/api/dead-letter?limit=-1",
        "/api/metrics?hours=99999999999",
        "/api/jobs/timeline?hours=0",
    ],
)
async def test_bad_paging_and_window_values_are_rejected(app, path):
    httpx = pytest.importorskip("httpx")

    transport = httpx.ASGITransport(app=create_dashboard_app(app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get(path)).status_code == 422


def test_can_write_needs_both_env_vars(monkeypatch):
    from soniq.dashboard.server import get_dashboard_html

    monkeypatch.setenv("SONIQ_DASHBOARD_WRITE_ENABLED", "true")
    monkeypatch.delenv("SONIQ_DASHBOARD_API_KEY", raising=False)
    without_key = get_dashboard_html()
    monkeypatch.setenv("SONIQ_DASHBOARD_API_KEY", "k")
    assert get_dashboard_html() != without_key
