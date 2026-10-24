"""Backend helper utilities."""

from datetime import datetime
from typing import Any, NoReturn


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


def dead_letter_row_to_dict(row: dict) -> dict:
    """Shape a soniq_dead_letter_jobs row like a job dict, status 'dead_letter'."""

    def iso(v: Any) -> Any:
        return v.isoformat() if isinstance(v, datetime) else v

    return {
        "id": str(row["id"]),
        "job_name": row["job_name"],
        "args": row["args"],
        "status": "dead_letter",
        "attempts": row["attempts"],
        "max_attempts": row["max_attempts"],
        "queue": row["queue"],
        "priority": row["priority"],
        "scheduled_at": None,
        "last_error": row["last_error"],
        "result": None,
        "dead_letter_reason": row["dead_letter_reason"],
        "created_at": iso(row["original_created_at"]),
        "updated_at": iso(row["moved_to_dead_letter_at"]),
    }
