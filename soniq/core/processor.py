"""
Job processing engine.

The backend-aware path: process_job_via_backend() is the only active
processing function. It fetches, executes, and updates jobs through
the StorageBackend abstraction.
"""

import asyncio
import concurrent.futures
import inspect
import logging
import time
import traceback
from typing import Any, List, Optional

from soniq.core.middleware import build_chain
from soniq.core.registry import JobRegistry
from soniq.core.retry import DEFAULT_RETRY_POLICY
from soniq.job import JobContext, Snooze
from soniq.observability.metrics import DEFAULT_METRICS_SINK
from soniq.settings import SoniqSettings

logger = logging.getLogger(__name__)

# Cap how much traceback + message we stash on a failed job. 8 KB is enough for
# a dozen-frame stack; bigger than that and one flaky handler can bloat the
# last_error column of the jobs table.
_MAX_LAST_ERROR_CHARS = 8192


def _format_job_error(exc: BaseException) -> str:
    """Render an exception as `ExceptionType: message\n<traceback>`.

    Keeps the full frame information in one string so operators can read the
    failure back from the job record (or the dashboard) without having to go
    hunt for logs. Truncated at `_MAX_LAST_ERROR_CHARS` to stay bounded.
    """
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    rendered = f"{type(exc).__name__}: {exc}\n{tb}"
    if len(rendered) > _MAX_LAST_ERROR_CHARS:
        rendered = rendered[:_MAX_LAST_ERROR_CHARS]
    return rendered


async def _execute_job_safely(
    job_record: dict,
    job_meta: dict,
    settings: Optional[SoniqSettings] = None,
    middleware: Optional[List[Any]] = None,
    sync_executor: Optional[concurrent.futures.ThreadPoolExecutor] = None,
    sync_pool_semaphore: Optional[asyncio.Semaphore] = None,
    in_flight_slot: Optional[dict] = None,
) -> tuple[bool, Optional[str], Any]:
    """
    Execute a job function safely with proper error handling.

    Args:
        job_record: Job record from database
        job_meta: Job metadata from registry
        middleware: Per-app middleware list, in registration order.
            ``None`` or empty means "no middleware" and skips the wrap.

    Returns:
        tuple: (success, error_message, return_value)
    """
    # Settings is optional so unit tests can drive this helper without
    # building a Soniq instance. Production paths (`process_job_via_backend`)
    # always thread the per-instance settings through; the fresh
    # `SoniqSettings()` fallback only fires for the bare-helper test path
    # and never consults the cached global.
    if settings is None:
        settings = SoniqSettings()

    # Backends uniformly return `args` as a dict (Postgres JSONB codec,
    # SQLite json.loads on read, Memory stores dict natively). A non-dict
    # at this point means a backend contract violation; surface it loudly.
    args_data = job_record["args"]
    if not isinstance(args_data, dict):
        raise ValueError(
            f"Backend contract violation: job args must be dict, got {type(args_data).__name__}"
        )

    # Validate arguments if model is specified
    args_model = job_meta.get("args_model")
    if args_model:
        try:
            validated_args = args_model(**args_data).model_dump()
        except (TypeError, ValueError, AttributeError) as validation_error:
            raise ValueError(
                f"Corrupted argument data: {str(validation_error)}"
            ) from validation_error
    else:
        validated_args = args_data

    # Inject JobContext if the function signature has it

    func = job_meta["func"]
    sig = inspect.signature(func)
    ctx = JobContext(
        job_id=str(job_record["id"]),
        job_name=job_record["job_name"],
        attempt=job_record["attempts"],
        max_attempts=job_record["max_attempts"],
        queue=job_record.get("queue", "default"),
        worker_id=str(job_record.get("worker_id", "")),
        scheduled_at=job_record.get("scheduled_at"),
        created_at=job_record.get("created_at"),
    )
    for param_name, param in sig.parameters.items():
        if param.annotation is JobContext:
            validated_args[param_name] = ctx
            break

    # Determine timeout: per-job overrides instance setting
    timeout = job_meta.get("timeout")
    if timeout is None:
        timeout = settings.job_timeout

    # Sync handlers run in a bounded ThreadPoolExecutor with post-claim
    # backpressure via an asyncio.Semaphore. Async handlers stay on the event
    # loop unchanged. The sync path is gated by acquiring the per-instance
    # semaphore *before* dispatch so saturation surfaces as a wait, not a
    # NACK / retry / rejection.
    #
    # JobRegistry wraps registered functions in a sync `def wrapper`
    # (functools.wraps-style passthrough), so `iscoroutinefunction(func)`
    # is False even when the underlying handler is `async def`. Unwrap
    # once via inspect.unwrap to consult the original.
    is_sync_handler = not inspect.iscoroutinefunction(inspect.unwrap(func))

    async def base_handler(_ctx: JobContext) -> Any:
        if is_sync_handler:
            if sync_executor is None or sync_pool_semaphore is None:
                # Fallback path for the bare-helper test entry point: no
                # bounded executor available, so run inline. Production
                # paths always thread the per-instance executor through.
                return func(**validated_args)

            loop = asyncio.get_running_loop()
            await sync_pool_semaphore.acquire()
            try:
                fut = sync_executor.submit(lambda: func(**validated_args))
            except BaseException:
                # Could not even submit; release the permit immediately.
                sync_pool_semaphore.release()
                raise

            # Tie semaphore release to the underlying thread, not to the
            # await path. asyncio.wait_for can raise TimeoutError while the
            # thread keeps running; releasing on the await timeout would let
            # a fresh sync job acquire a slot while the original thread
            # still holds an executor worker, breaking the
            # `active sync execution <= sync_handler_pool_size` invariant.
            #
            # The done-callback fires on whichever thread completes the
            # concurrent.futures.Future (typically the executor worker
            # thread). asyncio.Semaphore.release() is loop-affine, so we
            # post it back via call_soon_threadsafe.
            def _release_on_thread_done(_f: concurrent.futures.Future) -> None:
                loop.call_soon_threadsafe(sync_pool_semaphore.release)

            fut.add_done_callback(_release_on_thread_done)

            # Surface the future to the worker so the shutdown state
            # machine can tell sync work apart from async work.
            if in_flight_slot is not None:
                in_flight_slot["sync_future"] = fut

            try:
                # asyncio.wrap_future bridges into the loop. wait_for above
                # cancels this asyncio future on timeout, which forwards a
                # cancel attempt to the underlying concurrent future; if
                # the thread is already running, cancel is a no-op and the
                # thread continues. The semaphore stays held until the
                # thread truly returns (see done-callback above).
                return await asyncio.wrap_future(fut)
            finally:
                if in_flight_slot is not None:
                    in_flight_slot["sync_future"] = None
        else:
            # Coroutine handler; iscoroutinefunction is True.
            return await func(**validated_args)

    if middleware:
        invoke = build_chain(list(middleware), base_handler)
    else:
        invoke = base_handler

    try:
        if timeout:
            result = await asyncio.wait_for(invoke(ctx), timeout=timeout)
        else:
            result = await invoke(ctx)
        return True, None, result
    except asyncio.TimeoutError:
        return False, f"Job timed out after {timeout}s", None
    except Exception as e:
        return False, _format_job_error(e), None


async def _call_hooks(hooks: dict, hook_name: str, *args) -> None:
    """Call all registered hooks for an event, catching errors."""
    for fn in hooks.get(hook_name, []):
        try:
            if inspect.iscoroutinefunction(fn):
                await fn(*args)
            else:
                fn(*args)
        except Exception as e:
            logger.warning(f"Hook {hook_name} failed: {e}")


async def process_job_via_backend(
    backend: Any,
    job_registry: JobRegistry,
    queues: Optional[List[str]] = None,
    worker_id: Optional[str] = None,
    hooks: Optional[dict] = None,
    middleware: Optional[List[Any]] = None,
    retry_policy: Optional[Any] = None,
    metrics_sink: Optional[Any] = None,
    settings: Optional[SoniqSettings] = None,
    sync_executor: Optional[concurrent.futures.ThreadPoolExecutor] = None,
    sync_pool_semaphore: Optional[asyncio.Semaphore] = None,
    in_flight_slot: Optional[dict] = None,
) -> bool:
    """
    Process a single job using a StorageBackend.

    This is the primary processing path. It fetches, executes, and
    updates jobs through the backend abstraction.

    Args:
        backend: StorageBackend instance
        job_registry: JobRegistry for job lookup
        queues: Queue names to process from
        worker_id: Worker ID for tracking

    Returns:
        True if a job was processed, False if no jobs available
    """
    # Settings is per-instance: callers (Soniq workers, tests) supply the
    # resolved settings; we never reach for the cached global here.
    if settings is None:
        settings = SoniqSettings()

    _hooks = hooks or {}

    if retry_policy is None:
        retry_policy = DEFAULT_RETRY_POLICY

    if metrics_sink is None:
        metrics_sink = DEFAULT_METRICS_SINK

    job_record = await backend.fetch_and_lock_job(
        queues=queues,
        worker_id=worker_id,
    )
    if not job_record:
        return False

    # Clear any stale slot state from a prior iteration before populating
    # it for this job (slot is reused across the worker_task's lifetime).
    if in_flight_slot is not None:
        in_flight_slot.clear()

    job_id = str(job_record["id"])
    job_name = job_record["job_name"]
    max_attempts = job_record["max_attempts"]
    attempts = job_record["attempts"]

    start_time = time.time()
    logger.info(
        f"Processing job {job_id} ({job_name}) - attempt {attempts}/{max_attempts}"
    )

    # Guard: if repeated crashes pushed attempts past max, dead-letter without executing
    if attempts > max_attempts:
        await backend.mark_job_dead_letter(
            job_id,
            attempts=attempts,
            error="Max attempts exceeded (job crashed repeatedly)",
            reason="max_retries_exceeded",
        )
        logger.error(f"Job {job_id} dead-lettered after {attempts} crash attempts")
        return True

    job_meta = job_registry.get_job(job_name)
    if not job_meta:
        await backend.mark_job_dead_letter(
            job_id,
            attempts=max_attempts,
            error=f"Job {job_name} not registered.",
            reason="job_not_found",
        )
        logger.error(f"Job {job_name} not registered - moved to dead letter queue")
        return True

    queue_name = job_record.get("queue") or "default"
    await metrics_sink.record_job_start(
        job_id=job_id,
        job_name=job_name,
        queue=queue_name,
        attempt=attempts,
    )

    # Tell the worker (via its in-flight slot) what kind of handler this is
    # so the shutdown state machine can pick the right branch under
    # FORCE_TIMEOUT_PATH. The slot is a per-worker-task mutable dict; the
    # worker reads it directly when shutdown_timeout fires.
    func_for_kind = job_meta.get("func")
    is_sync_for_slot = not inspect.iscoroutinefunction(
        inspect.unwrap(func_for_kind) if func_for_kind is not None else func_for_kind
    )
    if in_flight_slot is not None:
        in_flight_slot["job_id"] = job_id
        in_flight_slot["is_sync"] = is_sync_for_slot
        in_flight_slot["sync_future"] = None

    try:
        return await _dispatch_and_record(
            backend=backend,
            settings=settings,
            retry_policy=retry_policy,
            metrics_sink=metrics_sink,
            hooks=_hooks,
            middleware=middleware,
            sync_executor=sync_executor,
            sync_pool_semaphore=sync_pool_semaphore,
            in_flight_slot=in_flight_slot,
            job_record=job_record,
            job_meta=job_meta,
            job_id=job_id,
            job_name=job_name,
            attempts=attempts,
            max_attempts=max_attempts,
            queue_name=queue_name,
            start_time=start_time,
        )
    finally:
        # Clear on exit so an idle worker_task between iterations does not
        # carry stale claim metadata into the next shutdown decision.
        if in_flight_slot is not None:
            in_flight_slot.clear()


async def _dispatch_and_record(
    *,
    backend: Any,
    settings: SoniqSettings,
    retry_policy: Any,
    metrics_sink: Any,
    hooks: dict,
    middleware: Optional[List[Any]],
    sync_executor: Optional[concurrent.futures.ThreadPoolExecutor],
    sync_pool_semaphore: Optional[asyncio.Semaphore],
    in_flight_slot: Optional[dict],
    job_record: dict,
    job_meta: dict,
    job_id: str,
    job_name: str,
    attempts: int,
    max_attempts: int,
    queue_name: str,
    start_time: float,
) -> bool:
    _hooks = hooks

    async def _emit_end(status: str, error: Optional[str] = None) -> None:
        await metrics_sink.record_job_end(
            job_id=job_id,
            job_name=job_name,
            queue=queue_name,
            status=status,
            duration_s=time.time() - start_time,
            error=error,
        )

    await _call_hooks(_hooks, "before_job", job_name, job_id, attempts)

    try:
        job_success, job_error, job_result = await _execute_job_safely(
            job_record,
            job_meta,
            settings=settings,
            middleware=middleware,
            sync_executor=sync_executor,
            sync_pool_semaphore=sync_pool_semaphore,
            in_flight_slot=in_flight_slot,
        )
    except ValueError as corruption_error:
        logger.error(f"Job {job_id} has corrupted data: {corruption_error}")
        await backend.mark_job_dead_letter(
            job_id,
            attempts=max_attempts,
            error=str(corruption_error),
            reason="invalid_arguments",
        )
        await _emit_end("dead_letter", error=str(corruption_error))
        return True

    duration_ms = round((time.time() - start_time) * 1000, 2)

    if job_success:
        if isinstance(job_result, Snooze):
            requested = float(job_result.seconds)
            if requested < 0:
                requested = 0.0
            capped = min(requested, settings.snooze_max_seconds)
            if capped < requested:
                logger.warning(
                    "Snooze capped from %.1fs to %.1fs (snooze_max_seconds) for job %s",
                    requested,
                    capped,
                    job_id,
                )
            # Snooze invariant: the dequeue path bumps `attempts` by one
            # the moment a worker claims the job (see fetch_and_lock_job in
            # each backend). A handler that returns Snooze(...) is asking
            # to defer, *not* to consume a retry slot. We undo the dequeue
            # bump by handing the backend `attempts - 1`, floored at 0.
            #
            # Concrete consequence: a job with max_retries=3 (max_attempts=4)
            # can snooze any number of times and still get its full 4
            # attempts when it eventually runs and either succeeds or
            # actually fails. Without this rollback, every snooze would
            # silently shrink the retry budget.
            restored_attempts = max(attempts - 1, 0)
            await backend.reschedule_job(
                job_id,
                delay_seconds=capped,
                attempts=restored_attempts,
                reason=job_result.reason,
            )
            logger.info(
                "Job %s snoozed for %.1fs (attempts kept at %d)",
                job_id,
                capped,
                restored_attempts,
            )
            await _emit_end("snoozed")
            return True

        await backend.mark_job_done(
            job_id, result_ttl=settings.result_ttl, result=job_result
        )
        logger.info(f"Job {job_id} completed in {duration_ms}ms")
        await _call_hooks(_hooks, "after_job", job_name, job_id, duration_ms)
        await _emit_end("done")
    else:
        await _call_hooks(
            _hooks, "on_error", job_name, job_id, str(job_error), attempts
        )
        if attempts >= max_attempts:
            wrapped = f"Max retries exceeded: {job_error}"
            if len(wrapped) > _MAX_LAST_ERROR_CHARS:
                wrapped = wrapped[:_MAX_LAST_ERROR_CHARS]
            await backend.mark_job_dead_letter(
                job_id,
                attempts=attempts,
                error=wrapped,
                reason="max_retries_exceeded",
            )
            logger.error(f"Job {job_id} moved to dead letter after {attempts} attempts")
            await _emit_end("dead_letter", error=str(job_error))
        else:
            # The original exception was consumed inside _execute_job_safely
            # (it's been formatted into job_error). Pass a synthetic
            # RuntimeError that carries the rendered message so a custom
            # RetryPolicy still has an `exc` to inspect; if a policy needs
            # the original type, the right answer is for the handler to
            # raise a typed error and the policy to look at its message.
            surfaced_exc = RuntimeError(job_error)
            retry_delay_value = retry_policy.delay_for(
                attempt=attempts,
                job_meta=job_meta,
                exc=surfaced_exc,
            )
            if retry_delay_value is None:
                wrapped = f"Retry policy declined: {job_error}"
                if len(wrapped) > _MAX_LAST_ERROR_CHARS:
                    wrapped = wrapped[:_MAX_LAST_ERROR_CHARS]
                await backend.mark_job_dead_letter(
                    job_id,
                    attempts=attempts,
                    error=wrapped,
                    reason="permanent_failure",
                )
                logger.error(
                    f"Job {job_id} dead-lettered by retry policy at attempt {attempts}"
                )
                await _emit_end("dead_letter", error=str(job_error))
                return True
            retry_delay = float(retry_delay_value)
            error_message = str(job_error)
            if len(error_message) > _MAX_LAST_ERROR_CHARS:
                error_message = error_message[:_MAX_LAST_ERROR_CHARS]
            await backend.mark_job_failed(
                job_id,
                attempts=attempts,
                error=error_message,
                retry_delay=retry_delay if retry_delay > 0 else None,
            )
            logger.warning(
                f"Job {job_id} failed (attempt {attempts}), "
                + (
                    f"retrying in {retry_delay:.1f}s"
                    if retry_delay > 0
                    else "retrying immediately"
                )
            )
            await _emit_end("failed", error=str(job_error))

    return True
