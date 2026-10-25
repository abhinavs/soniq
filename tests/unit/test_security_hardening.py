"""Small checks for the G2 hardening items (repr redaction, secrets, webhooks)."""

import hashlib
import hmac
import logging
import time

import asyncpg
import pytest
from pydantic import BaseModel

from soniq import Soniq, SoniqError
from soniq.features.signing import SecretManager
from soniq.features.webhooks import WebhookSigner
from soniq.settings import SoniqSettings


def test_settings_repr_hides_password():
    s = SoniqSettings(database_url="postgresql://u:hunter2@h/db")
    assert "hunter2" not in repr(s) and "hunter2" not in str(s)
    assert s.database_url == "postgresql://u:hunter2@h/db"


def test_is_encrypted_checks_fernet_shape(monkeypatch):
    monkeypatch.setenv("SONIQ_SECRET_KEY", "k")
    m = SecretManager()
    assert m.is_encrypted(m.encrypt("s" * 500))
    assert not m.is_encrypted("x" * 400)
    assert not m.is_encrypted("plain secret")


def test_decrypt_failure_is_logged(monkeypatch, caplog):
    monkeypatch.setenv("SONIQ_SECRET_KEY", "k1")
    token = SecretManager().encrypt("s")
    monkeypatch.setenv("SONIQ_SECRET_KEY", "k2")
    with caplog.at_level(logging.ERROR), pytest.raises(ValueError):
        SecretManager().decrypt(token)
    assert "SONIQ_SECRET_KEY" in caplog.text


def test_missing_key_warns(monkeypatch, caplog):
    monkeypatch.delenv("SONIQ_SECRET_KEY", raising=False)
    with caplog.at_level(logging.WARNING):
        SecretManager()
    assert "random key" in caplog.text


def test_v2_signature_covers_timestamp():
    ts, body = str(int(time.time())), '{"a": 1}'
    sig = WebhookSigner.sign_payload(f"{ts}.{body}", "sec")
    expected = hmac.new(b"sec", f"{ts}.{body}".encode(), hashlib.sha256).hexdigest()
    assert sig == f"sha256={expected}"


async def test_validate_error_omits_input_values():
    class Args(BaseModel):
        n: int

    app = Soniq(backend="memory")

    @app.job(name="g2.v", validate=Args)
    async def v(n: int): ...

    with pytest.raises(SoniqError) as ei:
        await app.enqueue(v, n="topsecret-value")
    assert "topsecret-value" not in str(ei.value)
    assert "n:" in str(ei.value)


async def test_asyncpg_connection_outside_transaction_rejected():
    class FakeConn(asyncpg.Connection):
        def __init__(self):
            pass

        def __del__(self):
            pass

        def is_in_transaction(self):
            return False

    from soniq.backends.postgres import PostgresBackend

    app = Soniq(database_url="postgresql://localhost/none")
    app._backend = PostgresBackend.__new__(PostgresBackend)
    app._initialized = True

    @app.job(name="g2.t")
    async def t(): ...

    with pytest.raises(SoniqError) as ei:
        await app.enqueue(t, connection=FakeConn())
    assert ei.value.error_code == "SONIQ_UNSUPPORTED_CONNECTION"


def test_dead_letter_tags_python_cannot_decode_read_as_none():
    from soniq.features.dead_letter import _tags

    assert _tags('{"a": "b"}') == {"a": "b"}
    assert _tags({"a": "b"}) == {"a": "b"}
    assert _tags("[" * 100_000 + "]" * 100_000) is None
