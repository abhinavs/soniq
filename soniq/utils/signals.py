"""
Signal handling utilities for graceful worker shutdown.

Provides cross-platform signal handling for production deployment scenarios
including Docker containers, systemd services, and Kubernetes pods.
"""

import asyncio
import logging
import signal
from typing import Optional, Set

logger = logging.getLogger(__name__)


class GracefulSignalHandler:
    """
    Handles common Unix signals for graceful worker shutdown.

    Supports:
    - SIGINT (Ctrl+C): Interactive interrupt
    - SIGTERM (kill command): Termination request (Docker, k8s, systemd)
    - SIGHUP (terminal disconnect): Hang up (systemd reload)
    """

    def __init__(self):
        self.shutdown_event: Optional[asyncio.Event] = None
        self.original_handlers: dict = {}
        self.handled_signals: Set[int] = set()

    def setup_signal_handlers(self, shutdown_event: asyncio.Event) -> None:
        """
        Setup signal handlers for graceful shutdown.

        Args:
            shutdown_event: Event to set when shutdown is requested
        """
        self.shutdown_event = shutdown_event

        # Define signals to handle
        signals_to_handle = []

        # SIGINT - Ctrl+C (always available)
        signals_to_handle.append(signal.SIGINT)

        # SIGTERM - Standard termination (Docker, k8s, systemd)
        if hasattr(signal, "SIGTERM"):
            signals_to_handle.append(signal.SIGTERM)

        # SIGHUP - Hang up (systemd reload, terminal disconnect)
        if hasattr(signal, "SIGHUP"):
            signals_to_handle.append(signal.SIGHUP)

        # Setup handlers for each signal
        for sig in signals_to_handle:
            try:
                # Store original handler
                original_handler = signal.signal(sig, self._signal_handler)
                self.original_handlers[sig] = original_handler
                self.handled_signals.add(sig)

                signal_name = signal.Signals(sig).name
                logger.debug(f"Registered signal handler for {signal_name}")

            except (OSError, ValueError) as e:
                # Some signals might not be available on all platforms
                signal_name = (
                    signal.Signals(sig).name if hasattr(signal, "Signals") else str(sig)
                )
                logger.debug(f"Could not register handler for {signal_name}: {e}")

    def _signal_handler(self, sig: int, frame) -> None:
        """
        Signal handler that triggers graceful shutdown.

        Args:
            sig: Signal number
            frame: Stack frame (unused)
        """
        try:
            signal_name = (
                signal.Signals(sig).name if hasattr(signal, "Signals") else str(sig)
            )
        except (ValueError, AttributeError):
            signal_name = str(sig)

        logger.info(f"Received {signal_name}, initiating graceful shutdown...")

        if self.shutdown_event:
            # Set the shutdown event to trigger worker cleanup
            # This is safe to call from a signal handler
            try:
                # Use asyncio.get_running_loop() to set the event safely
                loop = asyncio.get_running_loop()
                loop.call_soon_threadsafe(self.shutdown_event.set)
            except RuntimeError:
                # No running loop, set directly (should be safe in most cases)
                self.shutdown_event.set()

    def restore_signal_handlers(self) -> None:
        """
        Restore original signal handlers.

        Should be called during cleanup to restore default behavior.
        """
        for sig, original_handler in self.original_handlers.items():
            try:
                signal.signal(sig, original_handler)
                signal_name = (
                    signal.Signals(sig).name if hasattr(signal, "Signals") else str(sig)
                )
                logger.debug(f"Restored original handler for {signal_name}")
            except (OSError, ValueError) as e:
                logger.debug(f"Could not restore handler for signal {sig}: {e}")

        self.original_handlers.clear()
        self.handled_signals.clear()
        self.shutdown_event = None


# Global signal handler instance for the global API
_global_signal_handler: Optional[GracefulSignalHandler] = None


def setup_global_signal_handlers(
    shutdown_event: asyncio.Event,
) -> GracefulSignalHandler:
    """
    Setup signal handlers for the global API.

    Args:
        shutdown_event: Event to set when shutdown is requested

    Returns:
        GracefulSignalHandler instance for cleanup
    """
    global _global_signal_handler

    if _global_signal_handler is None:
        _global_signal_handler = GracefulSignalHandler()

    _global_signal_handler.setup_signal_handlers(shutdown_event)
    return _global_signal_handler


def cleanup_global_signal_handlers() -> None:
    """
    Cleanup global signal handlers.

    Restores original signal handlers to default behavior.
    """
    global _global_signal_handler

    if _global_signal_handler:
        _global_signal_handler.restore_signal_handlers()
        _global_signal_handler = None
