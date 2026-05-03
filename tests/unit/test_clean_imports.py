"""
Tests that the clean public import paths work.
"""


def test_schedules_importable_from_top_level():
    """from soniq import every, cron, daily, weekly, monthly"""
    from soniq import cron, daily, every, monthly, weekly

    assert callable(every)
    assert callable(cron)
    assert callable(daily)
    assert callable(weekly)
    assert callable(monthly)


def test_scheduler_service_importable_from_features():
    """from soniq.features.scheduler import Scheduler"""
    from soniq.features.scheduler import Scheduler

    assert callable(Scheduler)


def test_webhooks_importable_from_features():
    """from soniq.features.webhooks import WebhookService, WebhookTransport"""
    from soniq.features.webhooks import HTTPTransport, WebhookService, WebhookTransport

    assert callable(WebhookService)
    assert callable(HTTPTransport)
    assert WebhookTransport is not None


def test_dead_letter_importable_from_features():
    """from soniq.features.dead_letter import DeadLetterService"""
    from soniq.features.dead_letter import DeadLetterService

    assert callable(DeadLetterService)
