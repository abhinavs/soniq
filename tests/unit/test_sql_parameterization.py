import logging
from unittest.mock import AsyncMock, MagicMock

import pytest


class TestCleanupStaleWorkersParameterized:
    """Verify PostgresBackend.cleanup_stale_workers uses parameterized INTERVAL."""

    @pytest.mark.asyncio
    async def test_interval_is_parameterized(self):
        from contextlib import asynccontextmanager

        from soniq.backends.postgres import PostgresBackend

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_conn.execute = AsyncMock(return_value="UPDATE 0")

        @asynccontextmanager
        async def _acquire():
            yield mock_conn

        @asynccontextmanager
        async def _txn():
            yield

        mock_conn.transaction = MagicMock(return_value=_txn())

        mock_pool = MagicMock()
        mock_pool.acquire = _acquire

        backend = PostgresBackend.__new__(PostgresBackend)
        backend._pool = mock_pool

        await backend.cleanup_stale_workers(120)

        fetch_call = mock_conn.fetch.call_args
        sql = fetch_call.args[0]
        # Should use ($1 || ' seconds')::INTERVAL pattern, not an f-string
        assert "($1 || ' seconds')::INTERVAL" in sql
        assert fetch_call.args[1] == "120"


class TestGetDeliveryStatsParameterized:
    """Verify get_delivery_stats uses parameterized INTERVAL for hours."""

    @pytest.mark.asyncio
    async def test_interval_hours_is_parameterized(self, monkeypatch):
        from contextlib import asynccontextmanager

        from soniq.features import webhooks
        from soniq.testing.helpers import make_app

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(
            return_value={
                "total_deliveries": 0,
                "successful_deliveries": 0,
                "failed_deliveries": 0,
                "pending_deliveries": 0,
                "avg_attempts": 0,
            }
        )
        mock_conn.fetch = AsyncMock(return_value=[])

        @asynccontextmanager
        async def fake_acquire():
            yield mock_conn

        # The service borrows connections through ``WebhookService._acquire``
        # (an async context manager). Replace that hop with a mock so the
        # SQL under test runs against ``mock_conn`` without touching Postgres.
        service = webhooks.WebhookService(make_app())
        monkeypatch.setattr(service, "_acquire", fake_acquire)
        await service.get_delivery_stats(hours=48)

        fetchrow_call = mock_conn.fetchrow.call_args
        sql = fetchrow_call.args[0]
        assert "($1 || ' hours')::INTERVAL" in sql
        assert fetchrow_call.args[1] == "48"


class TestSecretKeyNotLogged:
    """Verify that the auto-generated secret key value does not appear in log output."""

    def test_generated_key_not_in_logs(self, caplog, monkeypatch):
        pytest.importorskip("cryptography")
        # Remove any existing key so the manager generates one
        monkeypatch.delenv("SONIQ_SECRET_KEY", raising=False)

        # Reset the global manager so a fresh one is created
        import soniq.features.signing as signing_mod

        signing_mod._secret_manager = None

        with caplog.at_level(logging.DEBUG, logger="soniq.features.signing"):
            manager = signing_mod.SecretManager()

        # The warning message should not contain the actual key
        generated_key = manager._secret_key
        for record in caplog.records:
            assert (
                generated_key not in record.message
            ), "The generated secret key value must not appear in log messages"


class TestRowsAffectedHelper:
    """Verify _rows_affected correctly parses asyncpg status strings."""

    @pytest.mark.parametrize(
        "status_string,expected",
        [
            ("UPDATE 3", 3),
            ("DELETE 0", 0),
            ("INSERT 0 1", 1),
            ("", 0),
        ],
    )
    def test_rows_affected(self, status_string, expected):
        from soniq.backends.helpers import rows_affected

        assert rows_affected(status_string) == expected
