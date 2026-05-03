"""Soniq application instance."""

import asyncio
import logging
import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    List,
    Optional,
    ParamSpec,
    TypeVar,
    Union,
    overload,
)

import asyncpg
from pydantic import ValidationError

from .backends import StorageBackend
from .backends.postgres import PostgresBackend
from .backends.postgres.migration_runner import MigrationRunner
from .backends.sqlite import SQLiteBackend
from .core.naming import validate_task_name
from .core.queue import _normalize_scheduled_time
from .core.registry import JobRegistry
from .core.retry import DEFAULT_RETRY_POLICY
from .core.worker import Worker
from .dashboard.app import DashboardService
from .errors import (
    SONIQ_TASK_ARGS_INVALID,
    SONIQ_UNKNOWN_TASK_NAME,
    SoniqError,
)
from .features.dead_letter import DeadLetterService
from .features.logging import LogService
from .features.scheduler import Scheduler, _coerce_schedule
from .features.signing import SigningService
from .features.webhooks import HTTPTransport, WebhookService
from .observability.metrics import DEFAULT_METRICS_SINK
from .plugin import (
    PluginCLI,
    PluginDashboard,
    PluginMigrations,
    PluginRegistry,
    discover_plugins,
)
from .settings import SoniqSettings
from .task_ref import TaskRef
from .testing.memory_backend import MemoryBackend
from .utils.hashing import compute_args_hash
from .utils.producer_id import resolve_producer_id
from .utils.rate_limit import default_warner

if TYPE_CHECKING:
    from .types import QueueStats

_P = ParamSpec("_P")

logger = logging.getLogger(__name__)


def _pool_sizing_error(
    *, concurrency: int, pool_max_size: int, pool_headroom: int
) -> Optional["SoniqError"]:
    """Return an error if the pool is too small, or None if adequate. pool_max_size=0 disables the check."""
    if pool_max_size <= 0:
        return None
    required = concurrency + pool_headroom
    if required <= pool_max_size:
        return None
    return SoniqError(
        f"Connection pool too small: worker concurrency ({concurrency}) "
        f"plus reserved headroom ({pool_headroom}) requires {required} "
        f"connections, but pool_max_size is {pool_max_size}. "
        f"Raise SONIQ_POOL_MAX_SIZE, lower SONIQ_CONCURRENCY, "
        f"or reduce SONIQ_POOL_HEADROOM.",
        "SONIQ_POOL_TOO_SMALL",
        context={
            "concurrency": concurrency,
            "pool_headroom": pool_headroom,
            "pool_max_size": pool_max_size,
            "required_connections": required,
        },
    )


class Soniq:

    def __init__(
        self,
        database_url: Optional[str] = None,
        backend: Optional[Any] = None,
        retry_policy: Optional[Any] = None,
        metrics_sink: Optional[Any] = None,
        plugins: Optional[List[Any]] = None,
        autoload_plugins: bool = False,
        **settings_overrides: Any,
    ) -> None:
        self._initialized = False
        self._closed = False

        if isinstance(backend, str):
            backend = self._resolve_backend_name(backend, database_url)
        elif backend is None and database_url:
            backend = self._auto_detect_backend(database_url)
        self._backend: Optional[StorageBackend] = backend

        self._retry_policy = retry_policy or DEFAULT_RETRY_POLICY
        self._metrics_sink = metrics_sink or DEFAULT_METRICS_SINK

        # Only pass database_url to settings for postgres - SQLite and Memory use it as a file path.
        if database_url and self._backend is None:
            settings_overrides["database_url"] = database_url

        self._settings = SoniqSettings(**settings_overrides)

        self._job_registry = JobRegistry()
        self._hooks: dict[str, list[Any]] = {
            "before_job": [],
            "after_job": [],
            "on_error": [],
        }
        self._middleware: list[Any] = []
        self._scheduler: Optional[Any] = None
        self._webhooks: Optional[Any] = None
        self._dead_letter: Optional[Any] = None
        self._logs: Optional[Any] = None
        self._signing: Optional[Any] = None
        self._dashboard_data: Optional[Any] = None

        self._plugins: list[Any] = []
        self._cli = PluginCLI()
        self._dashboard = PluginDashboard()
        self._migrations = PluginMigrations()

        # Semaphore is loop-affine: created lazily because construction can happen outside an event loop.
        self._sync_executor: Optional[ThreadPoolExecutor] = None
        self._sync_pool_semaphore: Optional[asyncio.Semaphore] = None

        for plugin in plugins or []:
            self.use(plugin)
        if autoload_plugins:
            for plugin in discover_plugins():
                self.use(plugin)

        logger.debug("Created Soniq instance")

    def _check_setup_frozen(self, attr: str) -> None:
        """Raise if called after setup() - swapping policy/sink after the worker captures them would silently fork behavior."""
        if self._initialized:
            raise SoniqError(
                f"Cannot set {attr} after Soniq.setup() has run. "
                f"Configure pluggable extension points before the first "
                f"async call (or ``await app.setup()``).",
                "SONIQ_APP_FROZEN",
            )

    @property
    def retry_policy(self) -> Any:
        return self._retry_policy

    @retry_policy.setter
    def retry_policy(self, value: Any) -> None:
        self._check_setup_frozen("retry_policy")
        self._retry_policy = value

    @property
    def metrics_sink(self) -> Any:
        return self._metrics_sink

    @metrics_sink.setter
    def metrics_sink(self, value: Any) -> None:
        self._check_setup_frozen("metrics_sink")
        self._metrics_sink = value

    @staticmethod
    def _auto_detect_backend(database_url: str) -> Any:
        """Detect backend from URL: postgres:// -> None (lazy Postgres), *.db/*.sqlite -> SQLiteBackend."""
        if database_url.startswith(("postgresql://", "postgres://")):
            return None

        if database_url.endswith((".db", ".sqlite", ".sqlite3")):
            return SQLiteBackend(database_url)

        return None

    @staticmethod
    def _resolve_backend_name(name: str, database_url: Optional[str] = None) -> Any:
        """Resolve a string backend name to a backend instance."""
        if name == "memory":
            return MemoryBackend()
        elif name == "sqlite":
            path = database_url if database_url else "soniq.db"
            return SQLiteBackend(path)
        elif name == "postgres":
            return None
        else:
            raise ValueError(
                f"Unknown backend: {name!r}. Use 'postgres', 'sqlite', or 'memory'."
            )

    @property
    def settings(self) -> SoniqSettings:
        return self._settings

    @property
    def backend(self) -> Any:
        """None until _ensure_initialized() has run."""
        return self._backend

    @property
    def registry(self) -> JobRegistry:
        return self._job_registry

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    @property
    def is_closed(self) -> bool:
        return self._closed

    async def ensure_initialized(self) -> None:
        """Idempotent public init - safe to call before an explicit setup()."""
        await self._ensure_initialized()

    async def _ensure_initialized(self) -> None:
        if self._initialized:
            return

        if self._closed:
            raise SoniqError("Cannot use closed Soniq instance", "SONIQ_APP_CLOSED")

        try:
            logger.debug("Auto-initializing Soniq application...")
            if self._backend is None:
                self._backend = PostgresBackend(
                    database_url=self._settings.database_url,
                    pool_min_size=self._settings.pool_min_size,
                    pool_max_size=self._settings.pool_max_size,
                )

            await self._backend.initialize()

            self._initialized = True
            logger.debug("Soniq application initialized successfully")

        except Exception as e:
            logger.error(f"Failed to initialize Soniq application: {e}")
            await self._cleanup_on_error()
            raise SoniqError(
                f"Soniq initialization failed: {e}", "SONIQ_INIT_ERROR"
            ) from e

    def _check_pool_sizing(self, concurrency: int) -> None:
        """Raise if pool_max_size is too small - hard error so misconfiguration surfaces at startup, not at 3am."""
        if not isinstance(self._backend, PostgresBackend):
            return
        err = _pool_sizing_error(
            concurrency=concurrency,
            pool_max_size=self._settings.pool_max_size,
            pool_headroom=self._settings.pool_headroom,
        )
        if err is not None:
            raise err

    async def __aenter__(self) -> "Soniq":
        await self._ensure_initialized()
        return self

    async def __aexit__(
        self,
        exc_type: Any,
        exc_val: Any,
        exc_tb: Any,
    ) -> bool:
        await self.close()
        return False

    async def close(self) -> None:
        """Shutdown plugins in reverse install order, then close the backend. Plugin failures are logged and swallowed."""
        if self._closed:
            logger.warning("Soniq already closed")
            return

        logger.info("Closing Soniq application...")

        for plugin in reversed(self._plugins):
            hook = getattr(plugin, "on_shutdown", None)
            if hook is None:
                continue
            try:
                await hook(self)
            except Exception:
                logger.warning(
                    "Plugin %s on_shutdown failed; continuing.",
                    plugin.name,
                    exc_info=True,
                )

        try:
            if self._backend:
                await self._backend.close()
                logger.debug("Closed backend")

            # wait=False: the worker shutdown path already waited on in-flight sync threads.
            if self._sync_executor is not None:
                self._sync_executor.shutdown(wait=False)
                self._sync_executor = None
            self._sync_pool_semaphore = None

            self._closed = True
            self._initialized = False
            logger.info("Soniq application closed successfully")

        except Exception as e:
            logger.error(f"Error during Soniq application cleanup: {e}")
            self._closed = True
            self._initialized = False

    async def _reset(self) -> None:
        """Delete all jobs and workers. Used in test fixtures."""
        await self._ensure_initialized()
        await self._backend.reset()  # type: ignore[union-attr]

    def job(self, **kwargs: Any) -> Any:
        """Register a job. Use as ``@app.job(...)``; the bare ``@app.job`` form is not supported."""
        _JP = ParamSpec("_JP")
        _JR = TypeVar("_JR")

        # Pass per-instance route_map and task_name_pattern so validation is scoped
        # to this Soniq instance, not a global cache.
        route_map = dict(self._settings.route_map or {})
        task_name_pattern = self._settings.task_name_pattern

        def decorator(
            func: Callable[_JP, Awaitable[_JR]],
        ) -> Callable[_JP, Awaitable[_JR]]:
            return self._job_registry.register_job(
                func,
                _route_map=route_map,
                _task_name_pattern=task_name_pattern,
                **kwargs,
            )

        return decorator

    @property
    def scheduler(self) -> Any:
        """Lazy per-app Scheduler service."""
        if self._scheduler is None:
            self._scheduler = Scheduler(self)
        return self._scheduler

    @property
    def webhooks(self) -> Any:
        """Lazy per-app WebhookService (HTTP transport). Assign app._webhooks directly for a custom transport."""
        if self._webhooks is None:
            self._webhooks = WebhookService(self, transport=HTTPTransport())
        return self._webhooks

    @property
    def dead_letter(self) -> Any:
        """Lazy per-app `DeadLetterService`."""
        if self._dead_letter is None:
            self._dead_letter = DeadLetterService(self)
        return self._dead_letter

    @property
    def logs(self) -> Any:
        """Lazy per-app structured-log query service."""
        if self._logs is None:
            self._logs = LogService(self)
        return self._logs

    @property
    def signing(self) -> Any:
        """Lazy per-app `SigningService` (Fernet encryption helpers)."""
        if self._signing is None:
            self._signing = SigningService(self)
        return self._signing

    @property
    def dashboard_data(self) -> Any:
        """Lazy per-app DashboardService (data layer). The FastAPI surface lives in soniq.dashboard.server."""
        if self._dashboard_data is None:
            self._dashboard_data = DashboardService(self)
        return self._dashboard_data

    def periodic(
        self,
        *,
        cron: Any = None,
        every: Any = None,
        **job_kwargs: Any,
    ) -> Callable[..., Any]:
        """Register a recurring job. Pass cron= or every= (mutually exclusive)."""
        if cron is None and every is None:
            raise ValueError("@app.periodic requires either cron= or every=")
        if cron is not None and every is not None:
            raise ValueError("@app.periodic cannot accept both cron= and every=")

        # Validate at import time so errors surface early rather than at scheduler.start().
        schedule_type, schedule_value = _coerce_schedule(cron=cron, every=every)

        sched_args = job_kwargs.pop("schedule_args", None)

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            wrapped: Any = self.job(**job_kwargs)(func)
            retries = job_kwargs.get("retries")
            wrapped._soniq_periodic = {
                "cron": schedule_value if schedule_type == "cron" else None,
                "every": int(schedule_value) if schedule_type == "interval" else None,
                "args": sched_args or {},
                "queue": job_kwargs.get("queue"),
                "priority": job_kwargs.get("priority"),
                "max_attempts": (retries + 1) if retries is not None else None,
            }
            return wrapped  # type: ignore[no-any-return]

        return decorator

    def before_job(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        """Register a hook called before each job executes."""
        self._hooks["before_job"].append(fn)
        return fn

    def after_job(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        """Register a hook called after each job completes successfully."""
        self._hooks["after_job"].append(fn)
        return fn

    def on_error(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        """Register a hook called when a job fails."""
        self._hooks["on_error"].append(fn)
        return fn

    def middleware(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        """Register a middleware wrapping every job. Runs in registration order (first = outermost)."""
        self._middleware.append(fn)
        return fn

    def use(self, plugin: Any) -> Any:
        """Install a plugin. Raises SONIQ_PLUGIN_DUPLICATE on a name collision."""
        if any(p.name == plugin.name for p in self._plugins):
            raise SoniqError(
                f"Plugin {plugin.name!r} is already installed.",
                "SONIQ_PLUGIN_DUPLICATE",
                context={
                    "name": plugin.name,
                    "version": getattr(plugin, "version", "unknown"),
                },
            )
        plugin.install(self)
        self._plugins.append(plugin)
        logger.debug("Installed plugin %s (version %s)", plugin.name, plugin.version)
        return plugin

    @property
    def plugins(self) -> Any:
        """Read-only registry of installed plugins. Supports dict-style access and iteration."""
        return PluginRegistry(self._plugins)

    @property
    def cli(self) -> Any:
        return self._cli

    @property
    def dashboard(self) -> Any:
        return self._dashboard

    @property
    def migrations(self) -> Any:
        return self._migrations

    @overload
    async def enqueue(
        self,
        target: Callable[_P, Awaitable[Any]],
        /,
        *,
        queue: Optional[str] = ...,
        priority: Optional[int] = ...,
        scheduled_at: Union[datetime, timedelta, int, float, None] = ...,
        unique: Optional[bool] = ...,
        dedup_key: Optional[str] = ...,
        connection: Optional[Any] = ...,
        **func_kwargs: Any,
    ) -> str: ...

    @overload
    async def enqueue(
        self,
        target: str,
        /,
        *,
        args: Optional[dict[str, Any]] = ...,
        queue: Optional[str] = ...,
        priority: Optional[int] = ...,
        scheduled_at: Union[datetime, timedelta, int, float, None] = ...,
        unique: Optional[bool] = ...,
        dedup_key: Optional[str] = ...,
        connection: Optional[Any] = ...,
    ) -> str: ...

    @overload
    async def enqueue(
        self,
        target: "TaskRef",
        /,
        *,
        args: Optional[dict[str, Any]] = ...,
        queue: Optional[str] = ...,
        priority: Optional[int] = ...,
        scheduled_at: Union[datetime, timedelta, int, float, None] = ...,
        unique: Optional[bool] = ...,
        dedup_key: Optional[str] = ...,
        connection: Optional[Any] = ...,
    ) -> str: ...

    async def enqueue(
        self,
        target: Any,
        /,
        *,
        args: Optional[dict[str, Any]] = None,
        queue: Optional[str] = None,
        priority: Optional[int] = None,
        scheduled_at: Any = None,
        unique: Optional[bool] = None,
        dedup_key: Optional[str] = None,
        connection: Any = None,
        **func_kwargs: Any,
    ) -> str:
        """Enqueue a task. target can be a callable, string name, or TaskRef."""
        await self._ensure_initialized()
        assert self._backend is not None  # narrow type after init

        ref: Optional[TaskRef] = None
        if isinstance(target, TaskRef):
            ref = target
            job_name = ref.name
            if func_kwargs:
                raise TypeError(
                    "enqueue(TaskRef, ...) cannot accept **kwargs as function "
                    "arguments; pass args=dict instead."
                )
        elif isinstance(target, str):
            job_name = validate_task_name(target, self._settings.task_name_pattern)
            if func_kwargs:
                raise TypeError(
                    "enqueue('name', ...) cannot accept **kwargs as function "
                    "arguments (they would collide with enqueue options "
                    "like queue=, priority=); pass args=dict instead."
                )
        elif callable(target):
            if args is not None:
                raise TypeError(
                    "enqueue(callable, ...) cannot mix args=dict with "
                    "**kwargs; use one or the other."
                )
            job_name = (
                getattr(target, "_soniq_name", None)
                or f"{target.__module__}.{target.__name__}"
            )
            args = func_kwargs
        else:
            raise TypeError(
                f"enqueue() target must be a callable, string, or TaskRef; "
                f"got {type(target).__name__}"
            )

        if args is None:
            args = {}
        elif not isinstance(args, dict):
            raise TypeError(f"enqueue() args must be a dict, got {type(args).__name__}")

        if ref is not None and ref.args_model is not None:
            try:
                ref.args_model(**args)
            except ValidationError as e:
                raise SoniqError(
                    f"Invalid arguments for task {job_name!r}: {e}",
                    SONIQ_TASK_ARGS_INVALID,
                    context={"task_name": job_name},
                ) from e

        job_meta = self._job_registry.get_job(job_name)

        # TaskRef skips registry validation - the ref is the local declaration of the name.
        if job_meta is None and ref is None:
            mode = self._settings.enqueue_validation
            if mode == "strict":
                raise SoniqError(
                    f"Task '{job_name}' is not registered locally and "
                    f"SONIQ_ENQUEUE_VALIDATION is 'strict'.",
                    SONIQ_UNKNOWN_TASK_NAME,
                    context={"task_name": job_name, "mode": mode},
                    suggestions=[
                        "Register the task with @app.job(name=...) before enqueueing.",
                        "Or set SONIQ_ENQUEUE_VALIDATION=warn / =none if this "
                        "service is a pure producer with no local registry.",
                    ],
                )
            if mode == "warn":
                if default_warner().should_warn(job_name):
                    logger.warning(
                        "enqueue: task %r is not registered locally "
                        "(SONIQ_ENQUEUE_VALIDATION=warn); enqueueing anyway. "
                        "Further warnings for this name are rate-limited.",
                        job_name,
                    )
            # mode == "none" -> silent

        if job_meta is not None:
            args_model = job_meta.get("args_model")
            # Skip validation when the TaskRef arm already ran its own check.
            if args_model is not None and ref is None:
                try:
                    args_model(**args)
                except ValidationError as e:
                    raise SoniqError(
                        f"Invalid arguments for task {job_name!r}: {e}",
                        SONIQ_TASK_ARGS_INVALID,
                        context={"task_name": job_name},
                    ) from e
            final_priority = priority if priority is not None else job_meta["priority"]
            # Queue precedence: explicit queue= > ref.default_queue > registered queue > "default".
            if queue is not None:
                final_queue = queue
            elif ref is not None and ref.default_queue is not None:
                final_queue = ref.default_queue
            else:
                final_queue = job_meta["queue"]
            final_unique = unique if unique is not None else job_meta["unique"]
            max_attempts = job_meta["max_retries"] + 1
        else:
            final_priority = priority if priority is not None else 100
            if queue is not None:
                final_queue = queue
            elif ref is not None and ref.default_queue is not None:
                final_queue = ref.default_queue
            else:
                final_queue = "default"
            final_unique = unique if unique is not None else False
            max_attempts = self._settings.max_retries + 1

        scheduled_at = _normalize_scheduled_time(scheduled_at)
        args_hash = compute_args_hash(args) if final_unique else None
        job_id = str(uuid.uuid4())

        producer_id = resolve_producer_id(self._settings.producer_id)

        if connection is not None:
            if isinstance(self._backend, PostgresBackend):
                txn_id = await self._backend.create_job_transactional(
                    connection=connection,
                    job_id=job_id,
                    job_name=job_name,
                    args=args,
                    args_hash=args_hash,
                    max_attempts=max_attempts,
                    priority=final_priority,
                    queue=final_queue,
                    unique=final_unique,
                    dedup_key=dedup_key,
                    scheduled_at=scheduled_at,
                    producer_id=producer_id,
                )
                return txn_id or job_id
            raise ValueError(
                f"Transactional enqueue (connection=) is not supported by "
                f"{type(self._backend).__name__}. Use PostgresBackend for this feature."
            )

        result_id = await self._backend.create_job(
            job_id=job_id,
            job_name=job_name,
            args=args,
            args_hash=args_hash,
            max_attempts=max_attempts,
            priority=final_priority,
            queue=final_queue,
            unique=final_unique,
            dedup_key=dedup_key,
            scheduled_at=scheduled_at,
            producer_id=producer_id,
        )

        if self._backend.supports_push_notify:
            try:
                await self._backend.notify_new_job(final_queue)
            except Exception:
                logger.debug(
                    "notify_new_job failed for queue %s; workers will pick up via poll",
                    final_queue,
                    exc_info=True,
                )

        return result_id or job_id

    async def enqueue_many(
        self,
        target: Any,
        args_list: list[dict[str, Any]],
        *,
        queue: Optional[str] = None,
        priority: Optional[int] = None,
        scheduled_at: Any = None,
    ) -> list[str]:
        """Enqueue many jobs with the same target. Returns the list of job IDs in input order.

        On Postgres this issues a single multi-row INSERT; on SQLite/Memory it loops over
        ``create_job``. Does not support unique/dedup_key - if the registered job declares
        ``unique=True`` or you need per-job dedup, use ``enqueue()`` in a loop instead.
        """
        await self._ensure_initialized()
        assert self._backend is not None

        if not isinstance(args_list, list):
            raise TypeError(
                f"enqueue_many() args_list must be a list, got {type(args_list).__name__}"
            )
        if not args_list:
            return []

        ref: Optional[TaskRef] = None
        if isinstance(target, TaskRef):
            ref = target
            job_name = ref.name
        elif isinstance(target, str):
            job_name = validate_task_name(target, self._settings.task_name_pattern)
        elif callable(target):
            job_name = (
                getattr(target, "_soniq_name", None)
                or f"{target.__module__}.{target.__name__}"
            )
        else:
            raise TypeError(
                f"enqueue_many() target must be a callable, string, or TaskRef; "
                f"got {type(target).__name__}"
            )

        for i, args in enumerate(args_list):
            if not isinstance(args, dict):
                raise TypeError(
                    f"enqueue_many() args_list[{i}] must be a dict, "
                    f"got {type(args).__name__}"
                )

        if ref is not None and ref.args_model is not None:
            for i, args in enumerate(args_list):
                try:
                    ref.args_model(**args)
                except ValidationError as e:
                    raise SoniqError(
                        f"Invalid arguments for task {job_name!r} at index {i}: {e}",
                        SONIQ_TASK_ARGS_INVALID,
                        context={"task_name": job_name, "index": i},
                    ) from e

        job_meta = self._job_registry.get_job(job_name)

        if job_meta is None and ref is None:
            mode = self._settings.enqueue_validation
            if mode == "strict":
                raise SoniqError(
                    f"Task '{job_name}' is not registered locally and "
                    f"SONIQ_ENQUEUE_VALIDATION is 'strict'.",
                    SONIQ_UNKNOWN_TASK_NAME,
                    context={"task_name": job_name, "mode": mode},
                    suggestions=[
                        "Register the task with @app.job(name=...) before enqueueing.",
                        "Or set SONIQ_ENQUEUE_VALIDATION=warn / =none if this "
                        "service is a pure producer with no local registry.",
                    ],
                )
            if mode == "warn":
                if default_warner().should_warn(job_name):
                    logger.warning(
                        "enqueue_many: task %r is not registered locally "
                        "(SONIQ_ENQUEUE_VALIDATION=warn); enqueueing anyway.",
                        job_name,
                    )

        if job_meta is not None:
            args_model = job_meta.get("args_model")
            if args_model is not None and ref is None:
                for i, args in enumerate(args_list):
                    try:
                        args_model(**args)
                    except ValidationError as e:
                        raise SoniqError(
                            f"Invalid arguments for task {job_name!r} at index {i}: {e}",
                            SONIQ_TASK_ARGS_INVALID,
                            context={"task_name": job_name, "index": i},
                        ) from e
            if job_meta.get("unique"):
                raise TypeError(
                    f"enqueue_many() does not support unique jobs "
                    f"(task {job_name!r} declares unique=True). Use enqueue() in a loop."
                )
            final_priority = priority if priority is not None else job_meta["priority"]
            if queue is not None:
                final_queue = queue
            elif ref is not None and ref.default_queue is not None:
                final_queue = ref.default_queue
            else:
                final_queue = job_meta["queue"]
            max_attempts = job_meta["max_retries"] + 1
        else:
            final_priority = priority if priority is not None else 100
            if queue is not None:
                final_queue = queue
            elif ref is not None and ref.default_queue is not None:
                final_queue = ref.default_queue
            else:
                final_queue = "default"
            max_attempts = self._settings.max_retries + 1

        scheduled_at = _normalize_scheduled_time(scheduled_at)
        producer_id = resolve_producer_id(self._settings.producer_id)
        job_ids = [str(uuid.uuid4()) for _ in args_list]

        if isinstance(self._backend, PostgresBackend):
            await self._backend.create_jobs_bulk(
                job_ids=job_ids,
                job_name=job_name,
                args_list=args_list,
                max_attempts=max_attempts,
                priority=final_priority,
                queue=final_queue,
                scheduled_at=scheduled_at,
                producer_id=producer_id,
            )
        else:
            for jid, args in zip(job_ids, args_list):
                await self._backend.create_job(
                    job_id=jid,
                    job_name=job_name,
                    args=args,
                    args_hash=None,
                    max_attempts=max_attempts,
                    priority=final_priority,
                    queue=final_queue,
                    unique=False,
                    dedup_key=None,
                    scheduled_at=scheduled_at,
                    producer_id=producer_id,
                )

        if self._backend.supports_push_notify:
            try:
                await self._backend.notify_new_job(final_queue)
            except Exception:
                logger.debug(
                    "notify_new_job failed for queue %s; workers will pick up via poll",
                    final_queue,
                    exc_info=True,
                )

        return job_ids

    async def schedule(
        self,
        target: Any,
        run_at: Any,
        *,
        args: Optional[dict[str, Any]] = None,
        **kwargs: Any,
    ) -> str:
        """Enqueue with scheduled_at=run_at."""
        return await self.enqueue(target, args=args, scheduled_at=run_at, **kwargs)

    async def _get_pool(self) -> Any:
        """Raw pool access for test fixtures. Public callers use app.backend.acquire()."""
        await self._ensure_initialized()
        if not isinstance(self._backend, PostgresBackend):
            return None
        return self._backend._pool

    def _get_job_registry(self) -> JobRegistry:
        return self._job_registry

    def _get_sync_dispatch(self) -> tuple[ThreadPoolExecutor, asyncio.Semaphore]:
        """Lazy init of the sync executor/semaphore pair - can't construct at Soniq() time outside an event loop."""
        if self._sync_executor is None:
            self._sync_executor = ThreadPoolExecutor(
                max_workers=self._settings.sync_handler_pool_size,
                thread_name_prefix="soniq-sync",
            )
        if self._sync_pool_semaphore is None:
            self._sync_pool_semaphore = asyncio.Semaphore(
                self._settings.sync_handler_pool_size
            )
        return self._sync_executor, self._sync_pool_semaphore

    async def run_worker(
        self,
        concurrency: int = 4,
        run_once: bool = False,
        queues: Optional[List[str]] = None,
    ) -> Any:
        await self._ensure_initialized()
        assert self._backend is not None  # narrowed after _ensure_initialized

        self._check_pool_sizing(concurrency)

        sync_executor, sync_pool_semaphore = self._get_sync_dispatch()
        worker = Worker(
            backend=self._backend,
            registry=self._job_registry,
            settings=self._settings,
            hooks=self._hooks,
            middleware=self._middleware,
            retry_policy=self._retry_policy,
            metrics_sink=self._metrics_sink,
            sync_executor=sync_executor,
            sync_pool_semaphore=sync_pool_semaphore,
        )

        if not run_once:
            self._maybe_warn_periodic_without_scheduler()

        return await worker.run(
            concurrency=concurrency,
            run_once=run_once,
            queues=queues,
        )

    def _maybe_warn_periodic_without_scheduler(self) -> None:
        """Warn once when @periodic jobs exist but no scheduler is running. Suppress with SONIQ_SCHEDULER_SUPPRESS_WARNING=1."""
        if os.environ.get("SONIQ_SCHEDULER_SUPPRESS_WARNING", "").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }:
            return

        all_jobs = self._job_registry.list_jobs()
        periodic_jobs = [
            name
            for name, meta in all_jobs.items()
            if getattr(meta.get("func"), "_soniq_periodic", None) is not None
        ]
        if not periodic_jobs:
            return

        logger.warning(
            "Detected %d @periodic job(s) (%s) but `soniq worker` no longer "
            "runs the recurring scheduler. Start `soniq scheduler` as a "
            "separate process or those jobs will never fire. Suppress "
            "this warning with SONIQ_SCHEDULER_SUPPRESS_WARNING=1.",
            len(periodic_jobs),
            ", ".join(sorted(periodic_jobs)[:3])
            + (", ..." if len(periodic_jobs) > 3 else ""),
        )

    async def get_job(self, job_id: str) -> Optional[dict[str, Any]]:
        await self._ensure_initialized()
        return await self._backend.get_job(job_id)  # type: ignore[union-attr]

    async def get_result(
        self,
        job_id: str,
        *,
        result_model: Optional[Any] = None,
    ) -> Any:
        """Return the result of a completed job, optionally deserialized via result_model. None if not done or no result."""
        await self._ensure_initialized()
        job = await self._backend.get_job(job_id)  # type: ignore[union-attr]
        if not job or job.get("status") != "done":
            return None
        result = job.get("result")
        if result is None or result_model is None:
            return result
        validate = getattr(result_model, "model_validate", None)
        if callable(validate):
            return validate(result)
        if isinstance(result, dict):
            return result_model(**result)
        return result_model(result)

    async def cancel_job(self, job_id: str) -> bool:
        await self._ensure_initialized()
        return await self._backend.cancel_job(job_id)  # type: ignore[union-attr]

    async def delete_job(self, job_id: str) -> bool:
        await self._ensure_initialized()
        return await self._backend.delete_job(job_id)  # type: ignore[union-attr]

    async def list_jobs(
        self,
        queue: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[dict[str, Any]]:
        await self._ensure_initialized()
        return await self._backend.list_jobs(  # type: ignore[union-attr]
            queue=queue, status=status, limit=limit, offset=offset
        )

    async def get_queue_stats(self) -> "QueueStats":
        await self._ensure_initialized()
        assert self._backend is not None
        return await self._backend.get_queue_stats()

    async def _get_migration_status(
        self, version_filter: str | None = None
    ) -> dict[str, Any]:
        await self._ensure_initialized()

        migration_runner = MigrationRunner(
            plugin_sources=self._migrations.list_sources()
        )
        async with self._backend.acquire() as conn:  # type: ignore[union-attr]
            return await migration_runner._get_migration_status_with_connection(
                conn, version_filter=version_filter
            )

    async def _run_migrations(self, version_filter: str | None = None) -> int:
        await self._ensure_initialized()

        migration_runner = MigrationRunner(
            plugin_sources=self._migrations.list_sources()
        )
        async with self._backend.acquire() as conn:  # type: ignore[union-attr]
            return await migration_runner._run_migrations_with_connection(
                conn, version_filter=version_filter
            )

    async def setup(self) -> int:
        """Create the database if needed, run migrations, fire plugin on_startup hooks. Returns migration count."""
        will_use_postgres = self._backend is None or isinstance(
            self._backend, PostgresBackend
        )
        if will_use_postgres:
            await self._ensure_postgres_database_exists()

        await self._ensure_initialized()
        assert self._backend is not None

        applied = 0
        if isinstance(self._backend, PostgresBackend):
            applied = await self._run_migrations(version_filter="000")

        await self._run_plugin_startup_hooks()
        return applied

    async def _run_plugin_startup_hooks(self) -> None:
        for plugin in self._plugins:
            hook = getattr(plugin, "on_startup", None)
            if hook is None:
                continue
            await hook(self)

    async def _ensure_postgres_database_exists(self) -> None:
        url = self._settings.database_url
        if "/" not in url.split("@")[-1]:
            return  # No database name in URL

        parts = url.rsplit("/", 1)
        if len(parts) != 2:
            return

        server_url = parts[0] + "/postgres"
        db_name = parts[1].split("?")[0]

        # CREATE DATABASE can't use parameterized statements - reject non-identifiers to prevent broken quoting.
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", db_name):
            raise ValueError(
                f"Refusing to CREATE DATABASE for non-identifier name: "
                f"{db_name!r}. soniq auto-create only accepts database "
                f"names matching the SQL identifier pattern "
                f"[A-Za-z_][A-Za-z0-9_$]*; create the database manually if "
                f"you need an unusual name, then skip auto-create."
            )

        try:
            conn = await asyncpg.connect(server_url)
            try:
                exists = await conn.fetchval(
                    "SELECT 1 FROM pg_database WHERE datname = $1", db_name
                )
                if not exists:
                    await conn.execute(f'CREATE DATABASE "{db_name}"')
                    logger.info(f"Created database: {db_name}")
            finally:
                await conn.close()
        except ValueError:
            raise
        except Exception as e:
            logger.debug(f"Could not auto-create database: {e}")

    async def _cleanup_on_error(self) -> None:
        try:
            if self._backend is not None:
                await self._backend.close()
        except Exception as e:
            logger.debug(f"Error during cleanup: {e}")
