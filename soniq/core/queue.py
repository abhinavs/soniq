"""
Job queue validation helpers.

Argument validation and scheduled time normalization used by Soniq.enqueue().
"""

from datetime import datetime, timedelta, timezone
from typing import Optional, Union

from pydantic import ValidationError


def _validate_job_arguments(job_name: str, job_meta: dict, kwargs: dict) -> None:
    """
    Validate job arguments against the job's Pydantic model.

    Args:
        job_name: Name of the job function
        job_meta: Job metadata from registry
        kwargs: Arguments to validate

    Raises:
        ValueError: If arguments are invalid
    """
    args_model = job_meta.get("args_model")
    if args_model:
        try:
            args_model(**kwargs)  # Validate arguments
        except ValidationError as e:
            raise ValueError(f"Invalid arguments for job {job_name}: {e}") from e


def _normalize_scheduled_time(
    scheduled_at: Optional[Union[datetime, int, float, timedelta]],
) -> Optional[datetime]:
    """
    Normalize scheduled_at to a timezone-aware UTC datetime for database storage.

    Accepted inputs:
    - Timezone-aware datetimes are converted to UTC.
    - timedelta and numeric values are treated as offsets from now in UTC.
    - Naive datetimes are rejected. A naive datetime is ambiguous across hosts
      in different timezones, so silently treating it as local time produced
      schedules that drifted per deployment target.

    Args:
        scheduled_at: datetime (with tzinfo), timedelta, or seconds from now.

    Returns:
        Timezone-aware UTC datetime suitable for TIMESTAMPTZ storage, or None.
    """
    if scheduled_at is None:
        return None

    # Handle timedelta values (add to current UTC time)
    if isinstance(scheduled_at, timedelta):
        return datetime.now(timezone.utc) + scheduled_at

    # Handle numeric values (seconds from now)
    if isinstance(scheduled_at, (int, float)):
        return datetime.now(timezone.utc) + timedelta(seconds=scheduled_at)

    if scheduled_at.tzinfo is None:
        raise ValueError(
            "scheduled_at must be timezone-aware. "
            "Pass datetime.now(timezone.utc) or attach tzinfo. "
            "Naive datetimes are ambiguous across hosts and were silently "
            "interpreted as local time."
        )

    return scheduled_at.astimezone(timezone.utc)
