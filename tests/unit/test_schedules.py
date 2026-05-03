"""Unit tests for the cron-string DSL in `soniq.schedules`.

These exercise pure-Python builders only - no I/O, no app, no scheduler.
They lock in the cron expressions emitted for each shape so the readability
layer cannot drift away from the strings the scheduler stores in the
database.
"""

from datetime import timedelta

import pytest

from soniq import cron, daily, every, monthly, weekly


class TestEvery:
    def test_minutes(self):
        assert every(5).minutes() == "*/5 * * * *"

    def test_hours(self):
        assert every(2).hours() == "0 */2 * * *"

    def test_days(self):
        assert every(3).days() == "0 0 */3 * *"

    def test_seconds_returns_timedelta(self):
        # cron has no sub-minute granularity; seconds() bridges to `every=`.
        result = every(30).seconds()
        assert isinstance(result, timedelta)
        assert result.total_seconds() == 30

    def test_zero_or_negative_rejected(self):
        with pytest.raises(ValueError):
            every(0)
        with pytest.raises(ValueError):
            every(-5)


class TestDaily:
    def test_at_morning(self):
        assert daily().at("09:00") == "0 9 * * *"

    def test_at_afternoon(self):
        assert daily().at("15:30") == "30 15 * * *"

    def test_bare_default_is_midnight(self):
        # Bare daily() coerces to its `__str__`, which the scheduler
        # converts to a string when used as `cron=daily()`.
        assert str(daily()) == "0 0 * * *"

    def test_invalid_format_rejected(self):
        with pytest.raises(ValueError):
            daily().at("9am")
        with pytest.raises(ValueError):
            daily().at("25:00")
        with pytest.raises(ValueError):
            daily().at("12:60")


class TestWeekly:
    @pytest.mark.parametrize(
        "name,expected_dow",
        [
            ("sunday", 0),
            ("sun", 0),
            ("monday", 1),
            ("mon", 1),
            ("tuesday", 2),
            ("tue", 2),
            ("wednesday", 3),
            ("wed", 3),
            ("thursday", 4),
            ("thu", 4),
            ("friday", 5),
            ("fri", 5),
            ("saturday", 6),
            ("sat", 6),
        ],
    )
    def test_named_days(self, name, expected_dow):
        assert weekly().on(name).at("09:00") == f"0 9 * * {expected_dow}"

    def test_int_day(self):
        assert weekly().on(3).at("12:30") == "30 12 * * 3"

    def test_case_insensitive(self):
        assert weekly().on("MONDAY").at("06:00") == "0 6 * * 1"

    def test_invalid_day_rejected(self):
        with pytest.raises(ValueError):
            weekly().on("notaday")
        with pytest.raises(ValueError):
            weekly().on(7)


class TestMonthly:
    def test_on_day_at(self):
        assert monthly().on_day(15).at("12:00") == "0 12 15 * *"

    def test_first_of_month(self):
        assert monthly().on_day(1).at("00:00") == "0 0 1 * *"

    def test_invalid_day_rejected(self):
        with pytest.raises(ValueError):
            monthly().on_day(0)
        with pytest.raises(ValueError):
            monthly().on_day(32)


class TestCronPassthrough:
    def test_returns_input(self):
        assert cron("*/15 * * * *") == "*/15 * * * *"

    def test_rejects_non_string(self):
        with pytest.raises(TypeError):
            cron(123)


class TestBuildersStrInteropWithPeriodic:
    """`@app.periodic(cron=daily().at("09:00"))` works because the at()
    terminals return a plain string. Bare builders like `daily()` rely on
    __str__ for the same affordance when no terminal was called.
    """

    def test_terminal_returns_str(self):
        v = daily().at("09:00")
        assert isinstance(v, str)
        assert v == "0 9 * * *"

    def test_bare_builder_str_dunder(self):
        assert str(daily()) == "0 0 * * *"
        assert str(weekly()) == "0 0 * * 0"
        assert str(monthly()) == "0 0 1 * *"
