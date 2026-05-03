"""Job registry - maps job names to functions and config. Each Soniq instance owns one; no global registry."""

import functools
from typing import (  # noqa: F401
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
    ParamSpec,
    Type,
    TypeVar,
    Union,
)

from pydantic import BaseModel

from soniq.core.naming import validate_task_name
from soniq.settings import SoniqSettings

_P = ParamSpec("_P")
_R = TypeVar("_R")


class JobRegistry:
    def __init__(self) -> None:
        self._registry: Dict[str, Dict[str, Any]] = {}

    def register_job(
        self,
        func: Callable[_P, Awaitable[_R]],
        *,
        name: Optional[str] = None,
        retries: int = 3,
        max_retries: Optional[int] = None,
        args_model: Optional[Type[BaseModel]] = None,
        validate: Optional[Type[BaseModel]] = None,
        priority: int = 100,
        queue: str = "default",
        unique: bool = False,
        retry_delay: Optional[Union[int, float, List[Union[int, float]]]] = 0,
        retry_backoff: bool = False,
        retry_max_delay: Optional[Union[int, float]] = None,
        retry_jitter: bool = True,
        timeout: Optional[Union[int, float]] = None,
        _route_map: Optional[Dict[str, str]] = None,
        _task_name_pattern: Optional[str] = None,
        **kwargs: Any,
    ) -> Callable[_P, Awaitable[_R]]:
        # Derived names skip pattern validation - callers didn't choose them and module/qualname
        # segments may legitimately contain camelcase that the default pattern rejects.
        if name is None:
            job_name = f"{func.__module__}.{func.__name__}"
        else:
            # Fall back to a fresh SoniqSettings() instead of get_settings() to avoid the global cache.
            if _task_name_pattern is None:
                _task_name_pattern = SoniqSettings().task_name_pattern
            job_name = validate_task_name(name, _task_name_pattern)

        @functools.wraps(func)
        def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> Awaitable[_R]:
            return func(*args, **kwargs)

        effective_args_model = validate or args_model
        effective_max_retries = max_retries if max_retries is not None else retries

        # Queue precedence: explicit @app.job(queue=...) > route_map prefix match > "default".
        # Treat queue="default" as unset since we can't distinguish explicit from default at this level.
        effective_queue = queue
        if queue == "default" and _route_map:
            best: Optional[tuple[str, str]] = None
            for prefix, mapped in _route_map.items():
                if job_name.startswith(prefix):
                    if best is None or len(prefix) > len(best[0]):
                        best = (prefix, mapped)
            if best is not None:
                effective_queue = best[1]

        job_config = {
            "func": wrapper,
            "max_retries": effective_max_retries,
            "args_model": effective_args_model,
            "priority": priority,
            "queue": effective_queue,
            "unique": unique,
            "retry_delay": retry_delay,
            "retry_backoff": retry_backoff,
            "retry_max_delay": retry_max_delay,
            "retry_jitter": retry_jitter,
            "timeout": timeout,
            **kwargs,
        }

        self._registry[job_name] = job_config

        wrapper._soniq_name = job_name  # type: ignore[attr-defined]
        wrapper._soniq_config = job_config  # type: ignore[attr-defined]

        return wrapper

    def get_job(self, name: str) -> Optional[Dict[str, Any]]:
        return self._registry.get(name)

    def clear(self) -> None:
        """Clear all registered jobs. Primarily for testing."""
        self._registry.clear()

    def remove_job(self, name: str) -> bool:
        if name in self._registry:
            del self._registry[name]
            return True
        return False

    def list_jobs(self) -> Dict[str, Dict[str, Any]]:
        return self._registry.copy()

    def __len__(self) -> int:
        return len(self._registry)

    def __contains__(self, name: str) -> bool:
        return name in self._registry
