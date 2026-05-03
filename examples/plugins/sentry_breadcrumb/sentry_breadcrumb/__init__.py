"""
sentry_breadcrumb - example Soniq plugin.

Adds a breadcrumb to Sentry for every job execution. Demonstrates each
extension point a real plugin uses:

- A middleware that wraps the handler and pushes a span / breadcrumb.
- A ``before_job`` hook that records the claim.
- A CLI subcommand (``soniq sentry-test``) that emits a synthetic event.
- Plugin-owned settings via ``BaseSettings`` reading ``SONIQ_SENTRY_DSN``.

The plugin is intentionally small (~100 LOC) and depends on
``sentry-sdk`` only when ``SONIQ_SENTRY_DSN`` is configured. With no DSN
set, the breadcrumb / hook calls become no-ops so the plugin is safe to
install in tests / CI without a real Sentry project.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

from soniq.plugin import CommandSpec

__version__ = "0.1.0"

logger = logging.getLogger(__name__)


class SentrySettings(BaseSettings):
    """Plugin-owned settings.

    Each plugin reads its own env vars with a unique prefix so the core
    Soniq settings don't collect feature-specific knobs. Mirrors the
    pattern Soniq itself uses; nothing special.
    """

    model_config = SettingsConfigDict(env_prefix="SONIQ_SENTRY_", extra="ignore")

    dsn: Optional[str] = None
    environment: str = "production"
    breadcrumb_category: str = "soniq.job"


class SentryBreadcrumbPlugin:
    """A real ``SoniqPlugin`` exercising every extension point.

    Implements ``install``, ``on_startup``, and ``on_shutdown`` so the
    test suite can pin lifecycle ordering. ``install`` is purely
    synchronous (registers handlers); the Sentry SDK init runs in
    ``on_startup`` because it touches the network.
    """

    name = "sentry_breadcrumb"
    version = __version__

    def __init__(self, settings: Optional[SentrySettings] = None):
        self.settings = settings or SentrySettings()
        self._initialized = False

    # --- install: synchronous wiring ----------------------------------

    def install(self, app: Any) -> None:
        """Wire the plugin against ``app`` using public APIs only."""

        @app.middleware
        async def _add_breadcrumb(ctx, call_next):
            self._record_breadcrumb(ctx)
            return await call_next(ctx)

        @app.before_job
        def _log_claim(job_name, job_id, attempts):
            logger.info(
                "sentry_breadcrumb: claimed %s (id=%s, attempt=%s)",
                job_name,
                job_id,
                attempts,
            )

        app.cli.add_command(
            CommandSpec(
                name="sentry-test",
                help="Emit a synthetic Sentry event to verify wiring",
                description=(
                    "Sends a test message to the configured Sentry DSN. "
                    "Useful in CI to confirm the DSN and environment are "
                    "wired correctly without running a real job."
                ),
                handler=self._handle_test_command,
            )
        )

    # --- on_startup / on_shutdown: deferred I/O -----------------------

    async def on_startup(self, app: Any) -> None:
        """Initialize the Sentry SDK if a DSN is configured.

        Called from ``Soniq.setup()`` after migrations. With no DSN the
        plugin stays installed but inert - useful for tests / CI.
        """
        if not self.settings.dsn:
            logger.info("sentry_breadcrumb: no SONIQ_SENTRY_DSN set; running inert")
            return
        try:
            import sentry_sdk

            sentry_sdk.init(
                dsn=self.settings.dsn,
                environment=self.settings.environment,
            )
            self._initialized = True
            logger.info("sentry_breadcrumb: Sentry SDK initialized")
        except ImportError:
            logger.warning(
                "sentry_breadcrumb: SONIQ_SENTRY_DSN is set but sentry-sdk "
                "is not installed; install with `pip install sentry-sdk` "
                "to enable breadcrumbs."
            )

    async def on_shutdown(self, app: Any) -> None:
        """Flush any pending Sentry events before the app shuts down."""
        if not self._initialized:
            return
        try:
            import sentry_sdk

            sentry_sdk.flush(timeout=2.0)
        except Exception:
            logger.debug("sentry_breadcrumb: flush failed", exc_info=True)

    # --- internals ----------------------------------------------------

    def _record_breadcrumb(self, ctx: Any) -> None:
        """Emit a Sentry breadcrumb for the in-flight job. No-op when
        Sentry is not initialized so tests can run against this plugin
        without a DSN."""
        if not self._initialized:
            return
        try:
            import sentry_sdk

            sentry_sdk.add_breadcrumb(
                category=self.settings.breadcrumb_category,
                message=f"job {ctx.job_name}",
                level="info",
                data={
                    "job_id": ctx.job_id,
                    "queue": ctx.queue,
                    "attempt": ctx.attempt,
                },
            )
        except Exception:
            logger.debug("sentry_breadcrumb: breadcrumb failed", exc_info=True)

    def _handle_test_command(self, args: Any) -> int:
        """``soniq sentry-test`` handler. Sends one synthetic event."""
        if not self.settings.dsn:
            print("SONIQ_SENTRY_DSN is not set; nothing to send.")
            return 1
        try:
            import sentry_sdk

            sentry_sdk.init(
                dsn=self.settings.dsn,
                environment=self.settings.environment,
            )
            sentry_sdk.capture_message(
                "soniq sentry-test: synthetic event from CLI",
                level="info",
            )
            sentry_sdk.flush(timeout=5.0)
            print("Sent synthetic Sentry event.")
            return 0
        except ImportError:
            print(
                "sentry-sdk is not installed; install with " "`pip install sentry-sdk`."
            )
            return 1
        except Exception as e:
            print(f"sentry-test failed: {e}")
            return 1


# Entry-point factory - declared in pyproject.toml under
# ``[project.entry-points."soniq.plugins"]`` so operators can opt in
# via ``SONIQ_PLUGINS=sentry_breadcrumb`` or ``--plugins=sentry_breadcrumb``.
def factory() -> SentryBreadcrumbPlugin:
    """Return a fresh instance. Used by entry-point discovery."""
    return SentryBreadcrumbPlugin()


__all__ = ["SentryBreadcrumbPlugin", "SentrySettings", "factory"]
