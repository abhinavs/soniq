"""
Focused unit tests for ``soniq.features.logging.LogService``.

The previous file (``test_logging_lock.py``) tested a lock-based design that
no longer exists. The kernel of the contract worth pinning today is that
``LogService`` is bound to a specific ``Soniq`` instance and routes its
queries through that instance's ``LogAnalyzer`` - no module-level globals,
no implicit reach for the global Soniq app.
"""

from unittest.mock import AsyncMock

import pytest


def test_log_service_binds_to_explicit_app():
    """LogService stores the Soniq instance passed in and constructs an
    analyzer against it - it does not reach for the global app."""
    from soniq.features.logging import LogAnalyzer, LogService

    sentinel = object()
    service = LogService(sentinel)

    assert service._app is sentinel
    assert isinstance(service.analyzer, LogAnalyzer)
    assert service.analyzer._app is sentinel


@pytest.mark.asyncio
async def test_log_service_methods_delegate_to_analyzer():
    """The three public methods forward to the analyzer with the same args
    so the service stays a thin facade."""
    from soniq.features.logging import LogService

    service = LogService(object())
    service.analyzer = AsyncMock()
    service.analyzer.get_error_summary.return_value = {"total_errors": 0}
    service.analyzer.get_performance_logs.return_value = []
    service.analyzer.search_logs.return_value = []

    assert await service.get_error_summary(hours=12) == {"total_errors": 0}
    service.analyzer.get_error_summary.assert_awaited_once_with(12)

    assert await service.get_performance_logs(job_name="x", hours=6) == []
    service.analyzer.get_performance_logs.assert_awaited_once_with("x", 6)

    assert await service.search_logs("err", job_id="j", level="ERROR", hours=3) == []
    service.analyzer.search_logs.assert_awaited_once_with("err", "j", "ERROR", 3)


def test_no_module_level_global_factory():
    """The legacy ``_service()`` helper and module-level
    ``get_error_summary`` / ``get_performance_logs`` / ``search_logs``
    wrappers were removed in 0.0.2. Callers must construct a
    ``LogService(app)`` against an explicit ``Soniq`` instance."""
    import soniq.features.logging as logging_mod

    assert not hasattr(logging_mod, "_service")
    assert not hasattr(logging_mod, "get_error_summary")
    assert not hasattr(logging_mod, "get_performance_logs")
    assert not hasattr(logging_mod, "search_logs")
