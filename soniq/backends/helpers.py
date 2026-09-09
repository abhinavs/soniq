"""Backend helper utilities."""

from typing import NoReturn


def no_transactional_enqueue(backend_name: str) -> NoReturn:
    """Raise the standard error for a transactional-enqueue call on a backend
    that can't do it.

    Both entry points funnel here so the message is identical whichever one the
    caller hit: ``app.backend.acquire()`` (the documented pattern) and
    ``app.enqueue(..., connection=...)`` (calling it directly).
    """
    raise ValueError(
        f"Transactional enqueue is not supported by {backend_name}. It needs a "
        "real connection pool, so it requires the PostgreSQL backend "
        "(postgresql://...). See docs/guides/transactional-enqueue.md."
    )


def rows_affected(result: str) -> int:
    """Extract the number of affected rows from an asyncpg status string like 'UPDATE 3'."""
    try:
        return int(result.split()[-1])
    except (ValueError, IndexError):
        return 0
