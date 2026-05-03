"""Pure cron-string builders used to compose `@app.periodic(cron=...)`.

Each terminal returns a `str` (a 5-field cron expression). Builders are
small dataclasses whose `__str__` returns the same expression so callers
can pass `daily().at("09:00")` directly into `cron=` without having to
remember `.expr`.

Sub-minute scheduling cannot be expressed in cron, so `every(n).seconds()`
returns a `timedelta` instead - pair it with `@app.periodic(every=...)`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Union

__all__ = ["every", "daily", "weekly", "monthly", "cron", "EveryBuilder"]


_DAY_NAMES = {
    "sunday": 0,
    "sun": 0,
    "monday": 1,
    "mon": 1,
    "tuesday": 2,
    "tue": 2,
    "wednesday": 3,
    "wed": 3,
    "thursday": 4,
    "thu": 4,
    "friday": 5,
    "fri": 5,
    "saturday": 6,
    "sat": 6,
}


def _parse_hhmm(s: str) -> tuple[int, int]:
    if not isinstance(s, str) or ":" not in s:
        raise ValueError(f"Time must be in HH:MM format (24-hour); got {s!r}")
    try:
        hh, mm = s.split(":", 1)
        hour = int(hh)
        minute = int(mm)
    except (ValueError, AttributeError):
        raise ValueError(f"Time must be in HH:MM format (24-hour); got {s!r}")
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Time out of range (00:00-23:59); got {s!r}")
    return hour, minute


def _parse_day(day: Union[str, int]) -> int:
    if isinstance(day, int):
        if not 0 <= day <= 6:
            raise ValueError(f"Day-of-week int must be 0..6 (Sun=0); got {day}")
        return day
    if isinstance(day, str):
        key = day.strip().lower()
        if key not in _DAY_NAMES:
            raise ValueError(
                f"Day-of-week string must be a weekday name like 'monday' or 'mon'; got {day!r}"
            )
        return _DAY_NAMES[key]
    raise TypeError(f"Day-of-week must be str or int; got {type(day).__name__}")


@dataclass(frozen=True)
class EveryBuilder:
    """Builder returned by `every(n)`. Terminals return cron strings except
    `.seconds()` which returns a `timedelta` (cron has no sub-minute granularity).
    """

    n: int

    def __post_init__(self) -> None:
        if not isinstance(self.n, int) or self.n < 1:
            raise ValueError(f"every(n) requires a positive integer; got {self.n!r}")

    def seconds(self) -> timedelta:
        return timedelta(seconds=self.n)

    def minutes(self) -> str:
        return f"*/{self.n} * * * *"

    def hours(self) -> str:
        return f"0 */{self.n} * * *"

    def days(self) -> str:
        return f"0 0 */{self.n} * *"


@dataclass(frozen=True)
class DailyBuilder:
    """`daily().at("HH:MM")` -> cron string. `__str__` returns the cron expr
    so `cron=daily().at("09:00")` works without `.expr`.
    """

    expr: str = "0 0 * * *"

    def at(self, time: str) -> str:
        hour, minute = _parse_hhmm(time)
        return f"{minute} {hour} * * *"

    def __str__(self) -> str:
        return self.expr


@dataclass(frozen=True)
class WeeklyOnBuilder:
    day: int

    def at(self, time: str) -> str:
        hour, minute = _parse_hhmm(time)
        return f"{minute} {hour} * * {self.day}"


@dataclass(frozen=True)
class WeeklyBuilder:
    expr: str = "0 0 * * 0"

    def on(self, day: Union[str, int]) -> WeeklyOnBuilder:
        return WeeklyOnBuilder(day=_parse_day(day))

    def __str__(self) -> str:
        return self.expr


@dataclass(frozen=True)
class MonthlyOnDayBuilder:
    day: int

    def at(self, time: str) -> str:
        hour, minute = _parse_hhmm(time)
        return f"{minute} {hour} {self.day} * *"


@dataclass(frozen=True)
class MonthlyBuilder:
    expr: str = "0 0 1 * *"

    def on_day(self, day: int) -> MonthlyOnDayBuilder:
        if not isinstance(day, int) or not 1 <= day <= 31:
            raise ValueError(f"Day-of-month must be int 1..31; got {day!r}")
        return MonthlyOnDayBuilder(day=day)

    def __str__(self) -> str:
        return self.expr


def every(n: int) -> EveryBuilder:
    """`every(5).minutes()` -> `'*/5 * * * *'`. `every(30).seconds()` -> `timedelta(30)`."""
    return EveryBuilder(n=n)


def daily() -> DailyBuilder:
    """`daily().at("09:00")` -> `'0 9 * * *'`. Bare `daily()` -> midnight."""
    return DailyBuilder()


def weekly() -> WeeklyBuilder:
    """`weekly().on("monday").at("09:00")` -> `'0 9 * * 1'`. Bare `weekly()` -> Sunday midnight."""
    return WeeklyBuilder()


def monthly() -> MonthlyBuilder:
    """`monthly().on_day(15).at("12:00")` -> `'0 12 15 * *'`. Bare `monthly()` -> 1st midnight."""
    return MonthlyBuilder()


def cron(expr: str) -> str:
    """Identity passthrough so `cron("...")` reads the same as `daily().at(...)`."""
    if not isinstance(expr, str):
        raise TypeError(f"cron(expr) expects a str; got {type(expr).__name__}")
    return expr
