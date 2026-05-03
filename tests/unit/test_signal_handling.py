"""
Unit tests for signal handling utilities.

Tests the signal handling classes and functions in isolation.
"""

import asyncio
import signal
from unittest.mock import MagicMock, patch

from soniq.utils.signals import (
    GracefulSignalHandler,
    cleanup_global_signal_handlers,
    setup_global_signal_handlers,
)


class TestGracefulSignalHandler:
    """Test the GracefulSignalHandler class"""

    def test_init(self):
        """Test signal handler initialization"""
        handler = GracefulSignalHandler()

        assert handler.shutdown_event is None
        assert len(handler.original_handlers) == 0
        assert len(handler.handled_signals) == 0

    @patch("signal.signal")
    def test_setup_signal_handlers(self, mock_signal):
        """Test signal handler setup"""
        handler = GracefulSignalHandler()
        shutdown_event = asyncio.Event()

        # Mock signal.signal to return a fake original handler
        mock_original_handler = MagicMock()
        mock_signal.return_value = mock_original_handler

        # Setup handlers
        handler.setup_signal_handlers(shutdown_event)

        # Verify signal.signal was called for expected signals
        assert mock_signal.call_count >= 1  # At least SIGINT

        # Verify internal state
        assert handler.shutdown_event is shutdown_event
        assert len(handler.handled_signals) >= 1
        assert len(handler.original_handlers) >= 1

    @patch("signal.signal")
    def test_restore_signal_handlers(self, mock_signal):
        """Test signal handler restoration"""
        handler = GracefulSignalHandler()
        shutdown_event = asyncio.Event()

        # Mock original handler
        mock_original_handler = MagicMock()
        mock_signal.return_value = mock_original_handler

        # Setup and then restore
        handler.setup_signal_handlers(shutdown_event)
        initial_call_count = mock_signal.call_count

        handler.restore_signal_handlers()

        # Should have called signal.signal again to restore
        assert mock_signal.call_count >= initial_call_count

        # Internal state should be cleared
        assert handler.shutdown_event is None
        assert len(handler.handled_signals) == 0
        assert len(handler.original_handlers) == 0

    @patch("signal.signal")
    @patch("asyncio.get_running_loop")
    def test_signal_handler_with_running_loop(self, mock_get_loop, mock_signal):
        """Test signal handler when asyncio loop is running"""
        handler = GracefulSignalHandler()
        shutdown_event = asyncio.Event()

        # Mock running loop
        mock_loop = MagicMock()
        mock_get_loop.return_value = mock_loop

        # Setup handler
        mock_signal.return_value = MagicMock()
        handler.setup_signal_handlers(shutdown_event)

        # Get the signal handler function
        signal_handler_func = None
        for call_args in mock_signal.call_args_list:
            if call_args[0][0] == signal.SIGINT:  # Look for SIGINT handler
                signal_handler_func = call_args[0][1]
                break

        assert signal_handler_func is not None

        # Simulate signal
        signal_handler_func(signal.SIGINT, None)

        # Should have called call_soon_threadsafe
        mock_loop.call_soon_threadsafe.assert_called_once()

    @patch("signal.signal")
    @patch("asyncio.get_running_loop")
    def test_signal_handler_no_running_loop(self, mock_get_loop, mock_signal):
        """Test signal handler when no asyncio loop is running"""
        handler = GracefulSignalHandler()
        shutdown_event = asyncio.Event()

        # Mock no running loop
        mock_get_loop.side_effect = RuntimeError("No running loop")

        # Setup handler
        mock_signal.return_value = MagicMock()
        handler.setup_signal_handlers(shutdown_event)

        # Get the signal handler function
        signal_handler_func = None
        for call_args in mock_signal.call_args_list:
            if call_args[0][0] == signal.SIGINT:
                signal_handler_func = call_args[0][1]
                break

        assert signal_handler_func is not None

        # Simulate signal - should not raise exception
        signal_handler_func(signal.SIGINT, None)

        # Event should be set directly
        assert shutdown_event.is_set()


class TestGlobalSignalHandlers:
    """Test global signal handler utilities"""

    @patch("soniq.utils.signals.GracefulSignalHandler")
    def test_setup_global_signal_handlers(self, mock_handler_class):
        """Test global signal handler setup"""
        mock_handler = MagicMock()
        mock_handler_class.return_value = mock_handler

        shutdown_event = asyncio.Event()

        result = setup_global_signal_handlers(shutdown_event)

        # Should create and setup handler
        mock_handler_class.assert_called_once()
        mock_handler.setup_signal_handlers.assert_called_once_with(shutdown_event)
        assert result is mock_handler

    def test_cleanup_global_signal_handlers(self):
        """Test global signal handler cleanup"""
        from soniq.utils import signals

        # Mock the global handler
        mock_handler = MagicMock()
        original_handler = signals._global_signal_handler
        signals._global_signal_handler = mock_handler

        try:
            cleanup_global_signal_handlers()

            # Should restore and clear global handler
            mock_handler.restore_signal_handlers.assert_called_once()
        finally:
            # Restore original state
            signals._global_signal_handler = original_handler

    def test_cleanup_global_signal_handlers_no_handler(self):
        """Test cleanup when no global handler exists"""
        # Should not raise exception
        cleanup_global_signal_handlers()


class TestSignalHandlerRobustness:
    """Test signal handler robustness and error conditions"""

    @patch("signal.signal")
    def test_signal_setup_with_unavailable_signal(self, mock_signal):
        """Test graceful handling when some signals are unavailable"""
        handler = GracefulSignalHandler()
        shutdown_event = asyncio.Event()

        # Mock signal.signal to raise OSError for some signals
        def mock_signal_func(sig, handler_func):
            if sig == signal.SIGINT:
                return MagicMock()  # Success
            else:
                raise OSError("Signal not available")  # Simulate unavailable signal

        mock_signal.side_effect = mock_signal_func

        # Should not raise exception
        handler.setup_signal_handlers(shutdown_event)

        # Should have at least registered SIGINT
        assert signal.SIGINT in handler.handled_signals

    @patch("signal.signal")
    def test_signal_restoration_with_error(self, mock_signal):
        """Test signal restoration handles errors gracefully"""
        handler = GracefulSignalHandler()
        shutdown_event = asyncio.Event()

        # Setup succeeds
        mock_original = MagicMock()
        mock_signal.return_value = mock_original
        handler.setup_signal_handlers(shutdown_event)

        # Restoration fails
        mock_signal.side_effect = OSError("Cannot restore signal")

        # Should not raise exception
        handler.restore_signal_handlers()

        # State should still be cleared
        assert len(handler.handled_signals) == 0
        assert handler.shutdown_event is None

    def test_multiple_setup_calls(self):
        """Test that multiple setup calls don't break the handler"""
        handler = GracefulSignalHandler()
        shutdown_event1 = asyncio.Event()
        shutdown_event2 = asyncio.Event()

        # First setup
        with patch("signal.signal") as mock_signal:
            mock_signal.return_value = MagicMock()
            handler.setup_signal_handlers(shutdown_event1)
            first_call_count = mock_signal.call_count

            # Second setup should work
            handler.setup_signal_handlers(shutdown_event2)

            # Should have made additional calls
            assert mock_signal.call_count >= first_call_count
            assert handler.shutdown_event is shutdown_event2
