"""
Soniq: Async Job Queue for Python (Backed by PostgreSQL)

The public API is the ``Soniq`` instance. There is no process-global
Soniq in 0.0.2 - per ``docs/_internals/contracts/instance_boundary.md``, the only
state allowed to be process-global is logging configuration.

Usage::

    from soniq import Soniq

    app = Soniq(database_url="postgresql://...")

    @app.job(name="users.send_welcome")
    async def send_welcome(user_id: int):
        ...

    await app.enqueue("users.send_welcome", args={"user_id": 42})
    await app.run_worker()
"""

from importlib.metadata import PackageNotFoundError, version

from .app import Soniq
from .job import JobContext, JobStatus, Snooze
from .schedules import cron, daily, every, monthly, weekly
from .task_ref import TaskRef, task_ref

try:
    __version__ = version("soniq")
except PackageNotFoundError:
    __version__ = "0.0.0"

__all__ = [
    "Soniq",
    "JobContext",
    "JobStatus",
    "Snooze",
    "TaskRef",
    "task_ref",
    "every",
    "cron",
    "daily",
    "weekly",
    "monthly",
    "DASHBOARD_AVAILABLE",
]

try:
    from .dashboard.server import FASTAPI_AVAILABLE as DASHBOARD_AVAILABLE
except Exception:
    DASHBOARD_AVAILABLE = False
