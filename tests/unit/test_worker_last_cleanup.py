"""
Test that _last_cleanup is an instance variable, not a class variable.
"""

from soniq.core.registry import JobRegistry
from soniq.core.worker import Worker
from soniq.testing.memory_backend import MemoryBackend


def test_last_cleanup_is_instance_attribute():
    """_last_cleanup should be set in __init__, not as a class variable."""
    backend = MemoryBackend()
    registry = JobRegistry()
    w = Worker(backend=backend, registry=registry)

    # Should be an instance attribute, not just inherited from the class
    assert (
        "_last_cleanup" in w.__dict__
    ), "_last_cleanup should be an instance attribute set in __init__"
