"""
Tests for the split storage-backend Protocols.

The interface is split into three slices:

- ``JobStore``: enqueue / dequeue / status transitions / queries /
  notify / lifecycle. Required of every backend.
- ``WorkerStore``: worker tracking and heartbeat housekeeping.
- ``TaskRegistryStore``: observability metadata for "which workers
  handle which task names".

``StorageBackend`` is the marker Protocol composed of all three.

These tests pin the membership of each Protocol (so a future refactor
that drops or moves a method doesn't silently change the contract) and
verify that the three production backends - Postgres, SQLite, Memory -
satisfy every Protocol via structural typing.
"""

import inspect


def _get_protocol_methods(cls) -> list[str]:
    """Return the public method/property names defined on a Protocol class."""
    return [
        name
        for name in dir(cls)
        if not name.startswith("_")
        and (
            callable(getattr(cls, name, None))
            or isinstance(getattr(type(cls), name, None), property)
        )
    ]


# ---------------------------------------------------------------------------
# Protocol membership
# ---------------------------------------------------------------------------


def test_job_store_has_lifecycle_methods():
    from soniq.backends import JobStore

    members = _get_protocol_methods(JobStore)
    assert "initialize" in members
    assert "close" in members


def test_job_store_has_capability_properties():
    from soniq.backends import JobStore

    members = dir(JobStore)
    assert "supports_push_notify" in members
    assert "supports_transactional_enqueue" in members
    assert "supports_advisory_locks" in members
    # Dropped in S7: callers type-narrow with isinstance(..., PostgresBackend).
    assert "supports_connection_pool" not in members
    assert "supports_migrations" not in members


def test_job_store_has_crud_and_dequeue_methods():
    from soniq.backends import JobStore

    members = _get_protocol_methods(JobStore)
    for name in (
        "create_job",
        "fetch_and_lock_job",
        "notify_new_job",
        "listen_for_jobs",
        "mark_job_done",
        "mark_job_failed",
        "mark_job_dead_letter",
        "nack_job",
        "reschedule_job",
        "cancel_job",
        "delete_job",
        "get_job",
        "list_jobs",
        "get_queue_stats",
        "delete_expired_jobs",
        "reset",
    ):
        assert name in members, name


def test_job_store_has_no_worker_or_task_methods():
    """Worker tracking and task registry methods belong on the narrower
    Protocols. Pinning the split so they don't drift back into JobStore."""
    from soniq.backends import JobStore

    members = _get_protocol_methods(JobStore)
    for name in (
        "register_worker",
        "update_heartbeat",
        "mark_worker_stopped",
        "cleanup_stale_workers",
        "register_task_name",
        "list_registered_task_names",
    ):
        assert name not in members, name


def test_worker_store_has_only_worker_methods():
    from soniq.backends import WorkerStore

    members = _get_protocol_methods(WorkerStore)
    for name in (
        "register_worker",
        "update_heartbeat",
        "mark_worker_stopped",
        "cleanup_stale_workers",
    ):
        assert name in members, name
    # Job CRUD must not have leaked over.
    for name in ("create_job", "fetch_and_lock_job", "mark_job_done"):
        assert name not in members, name


def test_task_registry_store_has_only_task_registry_methods():
    from soniq.backends import TaskRegistryStore

    members = _get_protocol_methods(TaskRegistryStore)
    assert "register_task_name" in members
    assert "list_registered_task_names" in members
    for name in ("create_job", "register_worker", "fetch_and_lock_job"):
        assert name not in members, name


def test_storage_backend_marker_composes_three_protocols():
    from soniq.backends import (
        JobStore,
        StorageBackend,
        TaskRegistryStore,
        WorkerStore,
    )

    bases = StorageBackend.__mro__
    assert JobStore in bases
    assert WorkerStore in bases
    assert TaskRegistryStore in bases


def test_create_job_accepts_dedup_key():
    from soniq.backends import JobStore

    sig = inspect.signature(JobStore.create_job)
    assert "dedup_key" in sig.parameters


# ---------------------------------------------------------------------------
# Production backends satisfy every Protocol
# ---------------------------------------------------------------------------


def test_postgres_backend_implements_all_three_protocols():
    from soniq.backends import (
        JobStore,
        StorageBackend,
        TaskRegistryStore,
        WorkerStore,
    )
    from soniq.backends.postgres import PostgresBackend

    backend = PostgresBackend.__new__(PostgresBackend)
    assert isinstance(backend, JobStore)
    assert isinstance(backend, WorkerStore)
    assert isinstance(backend, TaskRegistryStore)
    assert isinstance(backend, StorageBackend)


def test_sqlite_backend_implements_all_three_protocols():
    import pytest

    pytest.importorskip("aiosqlite")
    from soniq.backends import (
        JobStore,
        StorageBackend,
        TaskRegistryStore,
        WorkerStore,
    )
    from soniq.backends.sqlite import SQLiteBackend

    backend = SQLiteBackend.__new__(SQLiteBackend)
    assert isinstance(backend, JobStore)
    assert isinstance(backend, WorkerStore)
    assert isinstance(backend, TaskRegistryStore)
    assert isinstance(backend, StorageBackend)


def test_memory_backend_implements_all_three_protocols():
    from soniq.backends import (
        JobStore,
        StorageBackend,
        TaskRegistryStore,
        WorkerStore,
    )
    from soniq.testing.memory_backend import MemoryBackend

    backend = MemoryBackend()
    assert isinstance(backend, JobStore)
    assert isinstance(backend, WorkerStore)
    assert isinstance(backend, TaskRegistryStore)
    assert isinstance(backend, StorageBackend)


# ---------------------------------------------------------------------------
# Structural typing
# ---------------------------------------------------------------------------


def test_structural_typing_for_job_store():
    """An object with the right methods should satisfy ``JobStore``
    without inheriting from it. Pins that the Protocol stays runtime-
    checkable and matches by shape, not by nominal subclassing."""
    from soniq.backends import JobStore

    class JobOnlyStub:
        supports_push_notify = False
        supports_transactional_enqueue = False
        supports_advisory_locks = False

        async def initialize(self): ...
        async def close(self): ...
        async def create_job(self, **kw): ...
        async def fetch_and_lock_job(self, **kw): ...
        async def notify_new_job(self, queue): ...
        async def listen_for_jobs(self, callback, channel=""): ...
        async def mark_job_done(self, job_id, **kw): ...
        async def mark_job_failed(self, job_id, **kw): ...
        async def mark_job_dead_letter(self, job_id, **kw): ...
        async def nack_job(self, job_id): ...
        async def reschedule_job(self, job_id, **kw): ...
        async def cancel_job(self, job_id): ...
        async def delete_job(self, job_id): ...
        async def get_job(self, job_id): ...
        async def list_jobs(self, **kw): ...
        async def get_queue_stats(self): ...
        async def delete_expired_jobs(self): ...
        async def reset(self): ...

    assert isinstance(JobOnlyStub(), JobStore)


def test_job_only_stub_is_not_storage_backend():
    """A backend that only implements ``JobStore`` is not a full
    ``StorageBackend``. Future broker-only backends will rely on this
    distinction to refuse worker mode at startup."""
    from soniq.backends import StorageBackend, WorkerStore

    class JobOnlyStub:
        supports_push_notify = False
        supports_transactional_enqueue = False
        supports_advisory_locks = False

        async def initialize(self): ...
        async def close(self): ...
        async def create_job(self, **kw): ...
        async def fetch_and_lock_job(self, **kw): ...
        async def notify_new_job(self, queue): ...
        async def listen_for_jobs(self, callback, channel=""): ...
        async def mark_job_done(self, job_id, **kw): ...
        async def mark_job_failed(self, job_id, **kw): ...
        async def mark_job_dead_letter(self, job_id, **kw): ...
        async def nack_job(self, job_id): ...
        async def reschedule_job(self, job_id, **kw): ...
        async def cancel_job(self, job_id): ...
        async def delete_job(self, job_id): ...
        async def get_job(self, job_id): ...
        async def list_jobs(self, **kw): ...
        async def get_queue_stats(self): ...
        async def delete_expired_jobs(self): ...
        async def reset(self): ...

    stub = JobOnlyStub()
    assert not isinstance(stub, WorkerStore)
    assert not isinstance(stub, StorageBackend)
