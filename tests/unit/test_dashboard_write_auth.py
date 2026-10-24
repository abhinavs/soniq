"""
Dashboard write endpoints need write mode enabled *and* a configured
``SONIQ_DASHBOARD_API_KEY``. The contract:
- SONIQ_DASHBOARD_WRITE_ENABLED is false/missing -> 403 for all writes.
- Writes enabled + API key set -> allowed (middleware already validated).
- Writes enabled + no API key -> 403, even from loopback. A reverse proxy
  on the same host connects from loopback with a loopback Host header.
"""

import pytest

fastapi = pytest.importorskip("fastapi")
HTTPException = fastapi.HTTPException


class _FakeClient:
    def __init__(self, host):
        self.host = host


class _FakeRequest:
    def __init__(self, *, client_host, host_header):
        self.client = _FakeClient(client_host) if client_host else None
        self.headers = {"host": host_header}


def _set_api_key(monkeypatch, api_key=None):
    if api_key is None:
        monkeypatch.delenv("SONIQ_DASHBOARD_API_KEY", raising=False)
    else:
        monkeypatch.setenv("SONIQ_DASHBOARD_API_KEY", api_key)


def _set_write_enabled(monkeypatch, enabled: bool) -> None:
    if enabled:
        monkeypatch.setenv("SONIQ_DASHBOARD_WRITE_ENABLED", "true")
    else:
        monkeypatch.delenv("SONIQ_DASHBOARD_WRITE_ENABLED", raising=False)


def test_writes_disabled_without_env_even_on_localhost(monkeypatch):
    from soniq.dashboard.server import _require_write_authorization

    _set_write_enabled(monkeypatch, enabled=False)
    _set_api_key(monkeypatch, api_key=None)
    req = _FakeRequest(client_host="127.0.0.1", host_header="localhost:6161")

    with pytest.raises(HTTPException) as exc:
        _require_write_authorization(req)
    assert exc.value.status_code == 403
    assert "SONIQ_DASHBOARD_WRITE_ENABLED" in exc.value.detail


@pytest.mark.parametrize(
    "client_host,host_header",
    [("127.0.0.1", "localhost:6161"), ("127.0.0.1", "127.0.0.1:6161")],
)
def test_loopback_write_without_api_key_is_403(monkeypatch, client_host, host_header):
    from soniq.dashboard.server import _require_write_authorization

    _set_write_enabled(monkeypatch, enabled=True)
    _set_api_key(monkeypatch, api_key=None)
    req = _FakeRequest(client_host=client_host, host_header=host_header)

    with pytest.raises(HTTPException) as exc:
        _require_write_authorization(req)
    assert exc.value.status_code == 403
    assert "SONIQ_DASHBOARD_API_KEY" in exc.value.detail


def test_remote_write_without_api_key_is_403_when_enabled(monkeypatch):
    from soniq.dashboard.server import _require_write_authorization

    _set_write_enabled(monkeypatch, enabled=True)
    _set_api_key(monkeypatch, api_key=None)
    req = _FakeRequest(client_host="10.0.0.5", host_header="dashboard.example.com")

    with pytest.raises(HTTPException) as exc:
        _require_write_authorization(req)
    assert exc.value.status_code == 403
    assert "SONIQ_DASHBOARD_API_KEY" in exc.value.detail


def test_remote_write_with_api_key_set_passes_through(monkeypatch):
    """When an API key is configured, the global middleware already
    validated it. The write guard treats the call as authorized."""
    from soniq.dashboard.server import _require_write_authorization

    _set_write_enabled(monkeypatch, enabled=True)
    _set_api_key(monkeypatch, api_key="secret")
    req = _FakeRequest(client_host="10.0.0.5", host_header="dashboard.example.com")

    _require_write_authorization(req)
