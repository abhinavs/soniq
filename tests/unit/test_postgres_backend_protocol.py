"""
Tests that PostgresBackend satisfies the StorageBackend protocol.
"""


def test_postgres_backend_importable():
    from soniq.backends.postgres import PostgresBackend

    assert PostgresBackend is not None


def test_postgres_backend_satisfies_protocol():
    from soniq.backends import StorageBackend
    from soniq.backends.postgres import PostgresBackend

    backend = PostgresBackend.__new__(PostgresBackend)
    assert isinstance(backend, StorageBackend)


def test_postgres_backend_supports_push_notify():
    from soniq.backends.postgres import PostgresBackend

    backend = PostgresBackend.__new__(PostgresBackend)
    assert backend.supports_push_notify is True


def test_postgres_backend_supports_transactional_enqueue():
    from soniq.backends.postgres import PostgresBackend

    backend = PostgresBackend.__new__(PostgresBackend)
    assert backend.supports_transactional_enqueue is True


def test_postgres_backend_has_create_job_transactional():
    """Postgres-specific method, not on the protocol."""
    from soniq.backends.postgres import PostgresBackend

    assert hasattr(PostgresBackend, "create_job_transactional")
