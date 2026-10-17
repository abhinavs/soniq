"""FastAPI lifespan helper with an optional in-process worker.

    from soniq.integrations.fastapi import lifespan

    app = FastAPI(lifespan=lifespan(soniq, run_worker=True, concurrency=2))

Requires FastAPI (or Starlette) in your environment. Nothing here imports it.
"""

import asyncio
import logging
import os
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any, AsyncIterator, Callable, List, Optional

from soniq import Soniq

logger = logging.getLogger(__name__)


def lifespan(
    soniq: Soniq,
    *,
    run_worker: bool = False,
    concurrency: int = 2,
    queues: Optional[List[str]] = None,
    extra: Optional[Callable[[Any], Any]] = None,
) -> Callable[[Any], Any]:
    """Build a lifespan for ``FastAPI(lifespan=...)``.

    On startup it initializes ``soniq`` and, if ``run_worker`` is true, starts a
    worker task in the same event loop. On shutdown it stops the worker with the
    normal graceful path (in-flight jobs finish, or go back to the queue when
    ``shutdown_timeout`` expires), then closes ``soniq``.

    Each process that runs this lifespan starts its own worker. With
    ``uvicorn --workers 4`` you get four workers, so up to
    ``4 x concurrency`` jobs run at once.

    ``extra`` is your own lifespan (a function taking the app and returning an
    async context manager). It runs inside this one: it starts after Soniq is
    ready and stops before the worker stops.
    """

    @asynccontextmanager
    async def _lifespan(app: Any) -> AsyncIterator[None]:
        await soniq.ensure_initialized()
        stop = asyncio.Event()
        worker_task: Optional["asyncio.Task[Any]"] = None
        try:
            if run_worker:
                logger.info(
                    "Starting in-process Soniq worker (pid %d, concurrency %d). "
                    "Every process that runs this lifespan starts its own worker.",
                    os.getpid(),
                    concurrency,
                )
                worker_task = asyncio.create_task(
                    soniq.run_worker(
                        concurrency=concurrency, queues=queues, shutdown_event=stop
                    )
                )
                worker_task.add_done_callback(_log_worker_exit)
            async with AsyncExitStack() as stack:
                if extra is not None:
                    await stack.enter_async_context(extra(app))
                yield
        finally:
            try:
                if worker_task is not None:
                    stop.set()
                    try:
                        await worker_task
                    except Exception:
                        pass  # already logged by the done callback
            finally:
                await soniq.close()

    return _lifespan


def _log_worker_exit(task: "asyncio.Task[Any]") -> None:
    if not task.cancelled() and task.exception() is not None:
        logger.error("In-process Soniq worker stopped", exc_info=task.exception())
