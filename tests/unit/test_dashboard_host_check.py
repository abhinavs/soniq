"""A keyless dashboard only answers to loopback Host headers."""

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from soniq import Soniq  # noqa: E402
from soniq.dashboard import create_dashboard_app  # noqa: E402


def _status(monkeypatch, host, *, key="", allowed=""):
    monkeypatch.setenv("SONIQ_DASHBOARD_API_KEY", key)
    monkeypatch.setenv("SONIQ_DASHBOARD_ALLOWED_HOSTS", allowed)
    app = create_dashboard_app(Soniq(backend="memory"))
    return TestClient(app).get("/", headers={"Host": host}).status_code


@pytest.mark.parametrize("host", ["localhost:6161", "127.0.0.1", "[::1]:6161"])
def test_loopback_hosts_are_accepted(monkeypatch, host):
    assert _status(monkeypatch, host) == 200


def test_rebound_host_is_rejected_without_a_key(monkeypatch):
    assert _status(monkeypatch, "evil.example.com:6161") == 400


def test_allowed_hosts_env_opens_a_hostname(monkeypatch):
    assert _status(monkeypatch, "jobs.corp:80", allowed="jobs.corp") == 200


def test_any_host_is_fine_with_a_key(monkeypatch):
    assert _status(monkeypatch, "evil.example.com", key="k") == 200


async def test_run_dashboard_on_a_public_host_prints_the_hint(monkeypatch, capsys):
    from unittest import mock

    import soniq.dashboard.server as server

    monkeypatch.delenv("SONIQ_DASHBOARD_API_KEY", raising=False)
    monkeypatch.setattr(server, "create_dashboard_app", mock.Mock())
    monkeypatch.setattr(server.uvicorn, "Server", mock.Mock())
    server.uvicorn.Server.return_value.serve = mock.AsyncMock()

    await server.run_dashboard(None, host="0.0.0.0", port=6161)

    assert "SONIQ_DASHBOARD_ALLOWED_HOSTS" in capsys.readouterr().out


def test_every_response_carries_security_headers(monkeypatch):
    monkeypatch.setenv("SONIQ_DASHBOARD_API_KEY", "")
    client = TestClient(create_dashboard_app(Soniq(backend="memory")))
    for path in ("/", "/nope"):
        h = client.get(path, headers={"Host": "localhost"}).headers
        assert h["x-frame-options"] == "DENY"
        assert h["x-content-type-options"] == "nosniff"
        assert h["referrer-policy"] == "no-referrer"
        assert "frame-ancestors 'none'" in h["content-security-policy"]
    assert "fonts.googleapis.com" not in client.get("/").text


@pytest.mark.parametrize(
    "key,host,code", [("", "evil.example.com", 400), ("k", "localhost", 401)]
)
def test_rejections_carry_security_headers(monkeypatch, key, host, code):
    monkeypatch.setenv("SONIQ_DASHBOARD_API_KEY", key)
    client = TestClient(create_dashboard_app(Soniq(backend="memory")))
    r = client.get("/api/stats", headers={"Host": host})
    assert r.status_code == code
    assert r.headers["x-frame-options"] == "DENY"


def test_api_docs_pages_may_load_their_cdn_assets(monkeypatch):
    monkeypatch.setenv("SONIQ_DASHBOARD_API_KEY", "")
    client = TestClient(create_dashboard_app(Soniq(backend="memory")))
    for path in ("/docs", "/redoc"):
        csp = client.get(path, headers={"Host": "localhost"}).headers[
            "content-security-policy"
        ]
        assert "https://cdn.jsdelivr.net" in csp
        assert "frame-ancestors 'none'" in csp


async def test_run_dashboard_warns_when_writes_have_no_key(monkeypatch, capsys, caplog):
    from unittest import mock

    import soniq.dashboard.server as server

    monkeypatch.setenv("SONIQ_DASHBOARD_WRITE_ENABLED", "true")
    monkeypatch.delenv("SONIQ_DASHBOARD_API_KEY", raising=False)
    with mock.patch.object(server.uvicorn, "Server") as srv:
        srv.return_value.serve = mock.AsyncMock()
        await server.run_dashboard(Soniq(backend="memory"))
    assert "stays read-only" in capsys.readouterr().out
    assert "stays read-only" in caplog.text
