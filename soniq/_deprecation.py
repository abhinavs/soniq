"""Helpers for deprecating public names and arguments.

Set ``SONIQ_DEPRECATIONS=error`` to raise instead of warn (useful in test
suites). Set ``SONIQ_DEPRECATIONS=ignore`` to silence everything.
"""

import functools
import logging
import os
import warnings
from typing import Any, Callable, Set, TypeVar

logger = logging.getLogger("soniq")

F = TypeVar("F", bound=Callable[..., Any])

_logged: Set[str] = set()


class SoniqDeprecationWarning(DeprecationWarning):
    """Raised or warned when a deprecated Soniq feature is used."""


def warn_deprecated(
    what: str, *, since: str, removed_in: str, use_instead: str, stacklevel: int = 2
) -> None:
    """Warn that ``what`` is deprecated.

    Emits a ``SoniqDeprecationWarning`` (shown once per call site by the
    default Python filters) and logs the message once per process at WARNING,
    because Python hides ``DeprecationWarning`` outside ``__main__``.
    """
    mode = os.environ.get("SONIQ_DEPRECATIONS", "").strip().lower()
    if mode == "ignore":
        return

    message = (
        f"{what} is deprecated since Soniq {since} and will be removed in "
        f"{removed_in}. Use {use_instead} instead."
    )
    if mode == "error":
        raise SoniqDeprecationWarning(message)

    if message not in _logged:
        _logged.add(message)
        logger.warning(message)
    warnings.warn(message, SoniqDeprecationWarning, stacklevel=stacklevel)


def deprecated_param(
    old: str, *, use_instead: str, since: str, removed_in: str
) -> Callable[[F], F]:
    """Accept a renamed keyword argument and warn when the old name is used.

    The value passed as ``old`` is forwarded as ``use_instead``. Passing both
    raises ``TypeError``.
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if old in kwargs:
                if use_instead in kwargs:
                    raise TypeError(
                        f"{func.__name__}() got both '{old}' and '{use_instead}'"
                    )
                warn_deprecated(
                    f"The '{old}' argument of {func.__name__}()",
                    since=since,
                    removed_in=removed_in,
                    use_instead=f"'{use_instead}'",
                    stacklevel=3,
                )
                kwargs[use_instead] = kwargs.pop(old)
            return func(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator
