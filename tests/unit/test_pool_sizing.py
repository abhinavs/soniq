"""
Pool sizing is a load-bearing configuration: if worker concurrency plus the
reserved headroom exceeds `pool_max_size`, workers deadlock under load
waiting for a connection. The previous behavior logged a warning that
operators routinely missed. Now we refuse to start.
"""

import pytest

from soniq import Soniq
from soniq.app import _pool_sizing_error
from soniq.errors import SoniqError


def test_pool_sizing_error_triggers_when_undersized():
    err = _pool_sizing_error(concurrency=10, pool_max_size=5, pool_headroom=2)
    assert err is not None
    assert isinstance(err, SoniqError)
    assert err.error_code == "SONIQ_POOL_TOO_SMALL"
    # Actionable: both the numbers and the env vars belong in the message.
    rendered = str(err)
    assert "10" in rendered  # concurrency
    assert "5" in rendered  # pool_max_size
    assert "2" in rendered  # headroom
    assert "SONIQ_POOL_MAX_SIZE" in rendered


def test_pool_sizing_error_none_when_adequate():
    assert _pool_sizing_error(concurrency=4, pool_max_size=10, pool_headroom=2) is None


def test_pool_sizing_error_boundary_exact_fit_is_allowed():
    # pool_max_size == concurrency + headroom is exactly enough.
    assert _pool_sizing_error(concurrency=4, pool_max_size=6, pool_headroom=2) is None


def test_pool_sizing_error_off_by_one_triggers():
    # One connection short.
    err = _pool_sizing_error(concurrency=4, pool_max_size=5, pool_headroom=2)
    assert err is not None


def test_pool_sizing_error_zero_max_size_skipped():
    # pool_max_size=0 disables the check (some users set it explicitly).
    assert _pool_sizing_error(concurrency=4, pool_max_size=0, pool_headroom=2) is None


@pytest.mark.asyncio
async def test_run_worker_raises_when_pool_too_small(monkeypatch):
    """End-to-end: run_worker refuses to start with an undersized pool."""
    monkeypatch.setenv("SONIQ_DATABASE_URL", "postgresql://localhost/soniq")

    from soniq.backends.postgres import PostgresBackend

    app = Soniq(pool_max_size=2, pool_headroom=2)
    # Attach a real PostgresBackend (without initializing it) so the
    # isinstance check in `_check_pool_sizing` fires without requiring a
    # live database. The check reads pool_max_size off settings only.
    app._backend = PostgresBackend.__new__(PostgresBackend)
    app._backend._pool = None  # type: ignore[union-attr]
    app._initialized = True

    with pytest.raises(SoniqError, match="SONIQ_POOL_TOO_SMALL"):
        await app.run_worker(concurrency=4, run_once=True)


@pytest.mark.asyncio
async def test_run_worker_ok_with_adequate_pool(monkeypatch):
    """Regression guard: adequate pool does not raise."""
    monkeypatch.setenv("SONIQ_DATABASE_URL", "postgresql://localhost/soniq")

    app = Soniq(pool_max_size=10, pool_headroom=2)

    # With the memory backend there is no pool — check should be skipped.
    from soniq.testing.memory_backend import MemoryBackend

    app._backend = MemoryBackend()
    app._initialized = True

    # run_once processes no jobs (empty backend) and returns False.
    result = await app.run_worker(concurrency=4, run_once=True)
    assert result is False
