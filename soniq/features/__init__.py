"""Optional feature services for Soniq.

Each service is constructed against a ``Soniq`` instance and reached via
the lazy properties on the app (``app.webhooks``, ``app.dead_letter``,
``app.logs``, ``app.signing``, ``app.scheduler``). The submodules are
re-exported for callers that want direct access to the service classes
or the helper types (``WebhookTransport``, ``WebhookEvent``, ...).
"""

from . import (
    dead_letter,
    logging,
    scheduler,
    signing,
    webhooks,
)

__all__ = [
    "dead_letter",
    "logging",
    "scheduler",
    "signing",
    "webhooks",
]
