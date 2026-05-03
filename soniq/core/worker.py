"""
Soniq Worker

Processes jobs from the queue using a StorageBackend.
Handles concurrency, heartbeat, signal handling, and cleanup.
"""

import asyncio
import concurrent.futures
import logging
import os
import platform
import time
import uuid
from typing import Any, List, Optional

from ..backends import StorageBackend
from ..settings import SoniqSettings, get_settings
from ..utils.signals import GracefulSignalHandler
from .leadership import with_advisory_lock
from .processor import process_job_via_backend
from .registry import JobRegistry

logger = logging.getLogger(__name__)


class Worker:
    """
    Job processing worker.

    Fetches jobs from the backend, executes them, and updates status.
    Supports both run-once (process available jobs and exit) and
    continuous (long-running with LISTEN/NOTIFY) modes.
    """

    def __init__(
        self,
        backend: StorageBackend,
        registry: JobRegistry,
        settings: Optional[SoniqSettings] = None,
        hooks: Optional[dict] = None,
        middleware: Optional[List[Any]] = None,
        retry_policy: Optional[Any] = None,
        metrics_sink: Optional[Any] = None,
        sync_executor: Optional[concurrent.futures.ThreadPoolExecutor] = None,
        sync_pool_semaphore: Optional[asyncio.Semaphore] = None,
    ):
        self._backend: StorageBackend = backend
        self._registry = registry
        self._settings = settings or get_settings()
        self._last_cleanup = 0.0
        self._hooks = hooks or {}
        self._middleware = middleware or []
        self._retry_policy = retry_policy
        self._metrics_sink = metrics_sink
        # Bounded sync dispatch + post-claim semaphore. Both are
        # owned by the Soniq instance; the worker only reads them.
        self._sync_executor = sync_executor
        self._sync_pool_semaphore = sync_pool_semaphore

    async def run(
        self,
        concurrency: int = 4,
        run_once: bool = False,
        queues: Optional[List[str]] = None,
    ) -> bool:
        """
        Run the worker.

        Args:
            concurrency: Number of concurrent job processing tasks
            run_once: If True, process available jobs once and exit
            queues: Queue names to process. None means all queues.

        Returns:
            True if any jobs were processed
        """
        queue_info = "all queues" if queues is None else f"queues: {queues}"
        logger.info(
            f"Starting worker - concurrency: {concurrency}, processing: {queue_info}"
        )

        # Populate the soniq_task_registry observability table with the
        # names this worker handles. Best-effort; runs in both run_once
        # and continuous modes so the dashboard / tasks-check CLI sees a
        # consistent view regardless of how the worker was started.
        await self._populate_task_registry()

        try:
            if run_once:
                return await self.run_once(queues)
            else:
                return await self._run_continuous(concurrency, queues)
        finally:
            logger.info("Worker stopped")

    async def _populate_task_registry(self) -> None:
        """Upsert this worker's task names into the observability table.

        This table is observability metadata only. Failures here log at
        debug and do not block worker startup.
        """
        if not hasattr(self._backend, "register_task_name"):
            return

        # Generate a stable per-call worker_id for the observability row.
        # The continuous path uses its own worker_id (with heartbeat); for
        # run_once we just need a unique identifier so the upsert keys
        # don't collide across separate run_once invocations.
        worker_id = getattr(self, "_observability_worker_id", None) or str(uuid.uuid4())
        self._observability_worker_id = worker_id

        for task_name, meta in self._registry.list_jobs().items():
            args_model = meta.get("args_model")
            repr_str = (
                getattr(args_model, "__name__", repr(args_model))
                if args_model is not None
                else None
            )
            try:
                await self._backend.register_task_name(
                    task_name=task_name,
                    worker_id=worker_id,
                    args_model_repr=repr_str,
                )
            except Exception:
                logger.debug(
                    "register_task_name failed for %s; observability "
                    "table will be sparse but worker startup continues",
                    task_name,
                    exc_info=True,
                )

    async def run_once(
        self,
        queues: Optional[List[str]] = None,
        max_jobs: Optional[int] = None,
    ) -> bool:
        """
        Process available jobs once and exit.

        Args:
            queues: Queue names to process
            max_jobs: Max jobs to process. None means no limit.

        Returns:
            True if any jobs were processed
        """
        jobs_processed = 0

        while max_jobs is None or jobs_processed < max_jobs:
            processed = await process_job_via_backend(
                backend=self._backend,
                job_registry=self._registry,
                queues=queues,
                hooks=self._hooks,
                middleware=self._middleware,
                retry_policy=self._retry_policy,
                metrics_sink=self._metrics_sink,
                settings=self._settings,
            )

            if processed:
                jobs_processed += 1
            else:
                break

        return jobs_processed > 0

    async def _run_continuous(
        self, concurrency: int, queues: Optional[List[str]] = None
    ) -> bool:
        """Run continuous worker with heartbeat and signal handling."""
        shutdown_event = asyncio.Event()
        notification_event = asyncio.Event()
        signal_handler = GracefulSignalHandler()
        signal_handler.setup_signal_handlers(shutdown_event)

        # Set up LISTEN/NOTIFY if backend supports push notifications
        listen_handle = None
        if self._backend.supports_push_notify:

            def on_notification(connection, pid, channel, payload):
                logger.debug(f"Received notification on {channel}: {payload}")
                notification_event.set()

            try:
                listen_handle = await self._backend.listen_for_jobs(on_notification)
            except Exception as e:
                logger.warning(f"Failed to set up LISTEN/NOTIFY: {e}")

        # Start heartbeat if backend supports worker tracking
        heartbeat_task = None
        worker_id = None
        if hasattr(self._backend, "register_worker"):
            worker_id = str(uuid.uuid4())
            await self._backend.register_worker(
                worker_id=worker_id,
                hostname=platform.node(),
                pid=os.getpid(),
                queues=queues or [],
                concurrency=concurrency,
            )
            heartbeat_task = asyncio.create_task(
                self._heartbeat_loop(worker_id, shutdown_event)
            )

        # Per-worker-task in-flight slot. The processor populates each slot
        # at claim time so the shutdown state machine can pick the
        # async-vs-sync branch under FORCE_TIMEOUT_PATH (see
        # docs/_internals/contracts/shutdown.md).
        in_flight_slots: list[dict] = [{} for _ in range(concurrency)]

        async def worker_task(slot: dict):
            while not shutdown_event.is_set():
                try:
                    processed = await process_job_via_backend(
                        backend=self._backend,
                        job_registry=self._registry,
                        queues=queues,
                        worker_id=worker_id,
                        hooks=self._hooks,
                        middleware=self._middleware,
                        retry_policy=self._retry_policy,
                        metrics_sink=self._metrics_sink,
                        settings=self._settings,
                        sync_executor=self._sync_executor,
                        sync_pool_semaphore=self._sync_pool_semaphore,
                        in_flight_slot=slot,
                    )

                    if not processed:
                        # No jobs — wait for notification or timeout
                        notification_event.clear()
                        try:
                            # Wait for either shutdown, notification, or timeout
                            wait_tasks = [shutdown_event.wait()]
                            if self._backend.supports_push_notify:
                                wait_tasks.append(notification_event.wait())

                            done, _ = await asyncio.wait(
                                [asyncio.create_task(t) for t in wait_tasks],
                                timeout=self._settings.poll_interval,
                                return_when=asyncio.FIRST_COMPLETED,
                            )
                            # Cancel and await pending wait tasks
                            for t in _:
                                t.cancel()
                            await asyncio.gather(*_, return_exceptions=True)
                        except asyncio.TimeoutError:
                            pass  # Normal — check for jobs again

                    # Periodic cleanup
                    await self._maybe_cleanup()

                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.exception(f"Worker error: {e}")
                    await asyncio.sleep(self._settings.error_retry_delay)

        tasks = [
            asyncio.create_task(worker_task(in_flight_slots[i]))
            for i in range(concurrency)
        ]

        try:
            shutdown_task = asyncio.create_task(shutdown_event.wait())
            worker_gather = asyncio.ensure_future(
                asyncio.gather(*tasks, return_exceptions=True)
            )

            done, pending = await asyncio.wait(
                {worker_gather, shutdown_task},
                return_when=asyncio.FIRST_COMPLETED,
            )

            if shutdown_task in done:
                # Shutdown state machine. Source of truth:
                # docs/_internals/contracts/shutdown.md. RUNNING -> DRAINING here;
                # the per-task while-loop already stops fetching when
                # shutdown_event is set, so DRAINING is "wait up to
                # shutdown_timeout for the in-flight jobs". On expiry we
                # transition to FORCE_TIMEOUT_PATH and dispatch by
                # handler kind.
                logger.info(
                    "Graceful shutdown initiated; DRAINING (timeout=%.1fs)",
                    self._settings.shutdown_timeout,
                )
                await self._drain_then_force_timeout(
                    tasks=tasks,
                    in_flight_slots=in_flight_slots,
                )

            for task in pending:
                task.cancel()

        except KeyboardInterrupt:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            signal_handler.restore_signal_handlers()

            if heartbeat_task:
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass

            if worker_id and hasattr(self._backend, "mark_worker_stopped"):
                try:
                    await self._backend.mark_worker_stopped(worker_id)
                except Exception:
                    # Shutdown path: if the DB is already unreachable, the
                    # stale-worker sweep will mark this worker stopped later.
                    logger.debug(
                        "mark_worker_stopped failed for %s during shutdown",
                        worker_id,
                        exc_info=True,
                    )

            # Tear down the LISTEN handle. The handle owns its connection
            # and removes the listener + releases to the pool internally.
            if listen_handle is not None:
                await listen_handle.close()

        return True

    async def _drain_then_force_timeout(
        self,
        *,
        tasks: list[asyncio.Task],
        in_flight_slots: list[dict],
    ) -> None:
        """Implement the shutdown state machine.

        Step 1 - DRAINING: wait up to ``shutdown_timeout`` for every
        worker_task to exit cleanly. Each task already exits its outer
        while-loop the moment it observes ``shutdown_event`` between
        jobs, so this just gives whatever job is currently mid-flight
        time to finish.

        Step 2 - FORCE_TIMEOUT_PATH: branches by handler kind, read off
        each task's in-flight slot.

        - **Async branch:** cancel the worker_task (which surfaces a
          CancelledError up through the dispatch path), then call
          ``nack_job`` to put the row back to ``queued`` with attempts
          preserved. Total wall time stays bounded.
        - **Sync branch:** do **not** cancel. The thread is uncancelable
          from Python; the worker_task is awaiting an
          ``asyncio.wrap_future`` over the executor future and will
          unblock when the thread returns. Wait an extra
          ``sync_handler_grace_seconds`` (flat budget, measured from the
          FORCE_TIMEOUT_PATH instant). If the grace expires, enter
          ``WAIT_FOR_THREAD`` and keep waiting until the thread returns
          on its own or the operator's supervisor sends SIGKILL. Soniq
          never calls ``nack_job`` for sync handlers.
        """
        shutdown_timeout = self._settings.shutdown_timeout

        # ``asyncio.wait`` (unlike ``wait_for``) does *not* cancel pending
        # tasks on timeout; it just returns (done, pending). This matters
        # because we read each pending task's in-flight slot below, and
        # cancellation propagates through the processor's try/finally
        # which clears the slot - if we cancelled here, we'd lose the
        # job_id we need for nack_job.
        done, pending = await asyncio.wait(tasks, timeout=shutdown_timeout)
        if not pending:
            logger.info("All workers drained cleanly within shutdown_timeout")
            return

        logger.warning(
            "shutdown_timeout (%.1fs) elapsed; entering FORCE_TIMEOUT_PATH",
            shutdown_timeout,
        )

        # Snapshot the slot state at the FORCE_TIMEOUT_PATH instant. Slots
        # belong to running tasks; if a task is already done its slot has
        # been cleared by the processor's finally clause (or never
        # populated) and we just skip it.
        async_jobs: list[tuple[asyncio.Task, str]] = []
        sync_jobs: list[asyncio.Task] = []
        idle_tasks: list[asyncio.Task] = []
        for task, slot in zip(tasks, in_flight_slots):
            if task.done():
                continue
            is_sync = slot.get("is_sync")
            job_id = slot.get("job_id")
            if is_sync is True:
                sync_jobs.append(task)
            elif is_sync is False and job_id is not None:
                async_jobs.append((task, job_id))
            else:
                # No populated slot: task is between jobs (likely stuck
                # in poll_interval wait). Cancel it; nothing to NACK.
                idle_tasks.append(task)

        # Async branch: cancel + NACK. Cancellation surfaces as
        # CancelledError up the dispatch path; the row stays 'processing'
        # because mark_job_done/failed never ran. We then call nack_job
        # to flip it back to 'queued' with attempts preserved.
        for task, _ in async_jobs:
            task.cancel()
        for task in idle_tasks:
            task.cancel()

        # Wait for cancelled tasks. Bounded: cancellation propagates fast.
        cancelled_to_await = [t for t, _ in async_jobs] + idle_tasks
        if cancelled_to_await:
            await asyncio.gather(*cancelled_to_await, return_exceptions=True)

        for _, job_id in async_jobs:
            try:
                await self._backend.nack_job(job_id)
                logger.warning("shutdown_nack: job=%s reason=shutdown_timeout", job_id)
                if self._metrics_sink is not None:
                    emit = getattr(self._metrics_sink, "emit", None)
                    if callable(emit):
                        try:
                            res = emit(
                                "shutdown_nack",
                                {"job_id": job_id, "reason": "shutdown_timeout"},
                            )
                            if asyncio.iscoroutine(res):
                                await res
                        except Exception:
                            logger.debug(
                                "metrics_sink.emit shutdown_nack failed",
                                exc_info=True,
                            )
            except Exception:
                logger.warning(
                    "nack_job failed for %s during shutdown", job_id, exc_info=True
                )

        if not sync_jobs:
            return

        # Sync branch: wait the flat grace budget for the threads to
        # return on their own. Then enter WAIT_FOR_THREAD: keep waiting
        # forever (until the thread returns or supervisor SIGKILL ends
        # the process). docs/_internals/contracts/shutdown.md is explicit that
        # Soniq does not bound this tail.
        grace = self._settings.sync_handler_grace_seconds
        if grace is None:
            grace = self._settings.job_timeout or 0.0

        try:
            await asyncio.wait_for(
                asyncio.gather(*sync_jobs, return_exceptions=True),
                timeout=grace,
            )
            logger.info(
                "Sync handlers returned within sync_handler_grace_seconds (%.1fs)",
                grace,
            )
            return
        except asyncio.TimeoutError:
            pass

        logger.warning(
            "sync_handler_grace_seconds (%.1fs) elapsed; entering "
            "WAIT_FOR_THREAD (unbounded by Soniq alone). Operators must "
            "rely on supervisor SIGKILL to bound wall time.",
            grace,
        )
        await asyncio.gather(*sync_jobs, return_exceptions=True)

    async def _heartbeat_loop(
        self, worker_id: str, shutdown_event: asyncio.Event
    ) -> None:
        """Send periodic heartbeat updates."""
        interval = self._settings.heartbeat_interval
        while not shutdown_event.is_set():
            try:
                await self._backend.update_heartbeat(worker_id)
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Heartbeat failed: {e}")
                await asyncio.sleep(interval)

    async def _maybe_cleanup(self) -> None:
        """Run periodic cleanup if enough time has passed.

        Uses an advisory-lock leader guard so that, in multi-worker deployments,
        only one worker performs pruning and stale-worker cleanup per tick.
        Backends without advisory-lock support (Memory, SQLite) always run.
        """
        current = time.time()
        if current - self._last_cleanup < self._settings.cleanup_interval:
            return

        try:
            async with with_advisory_lock(self._backend, "soniq.maintenance") as leader:
                if leader:
                    await self._backend.delete_expired_jobs()
                    await self._backend.cleanup_stale_workers(
                        stale_threshold_seconds=int(self._settings.heartbeat_timeout),
                    )
            self._last_cleanup = current
        except Exception as e:
            logger.warning(f"Cleanup failed: {e}")
            self._last_cleanup = current
