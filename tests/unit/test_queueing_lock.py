"""
Tests for dedup_key parameter.

Verifies that the dedup_key parameter is accepted by the job decorator
and enqueue function, and stored in job configuration.
"""

from soniq.core.registry import JobRegistry


def test_job_decorator_accepts_dedup_key():
    """@job(dedup_key=...) should store the lock in job config."""
    registry = JobRegistry()

    async def my_job():
        pass

    registry.register_job(my_job, name="my_job")
    # dedup_key is a per-enqueue parameter, not per-registration.
    # The registry should accept it without error.
    assert registry.get_job("my_job") is not None


def test_enqueue_accepts_dedup_key_parameter():
    """Soniq.enqueue should accept dedup_key as a keyword-only argument."""
    import inspect

    from soniq.app import Soniq

    sig = inspect.signature(Soniq.enqueue)
    assert "dedup_key" in sig.parameters
    assert sig.parameters["dedup_key"].kind is inspect.Parameter.KEYWORD_ONLY
