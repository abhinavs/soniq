"""
Tests for JobRegistry operations not covered elsewhere.

Covers: clear, remove_job, list_jobs, __len__, __contains__.
"""

from soniq.core.registry import JobRegistry


def _make_registry_with_jobs():
    """Helper to create a registry with a few registered jobs."""
    registry = JobRegistry()

    async def task_a():
        pass

    async def task_b():
        pass

    async def task_c():
        pass

    registry.register_job(task_a, name="task_a", queue="emails")
    registry.register_job(task_b, name="task_b", queue="emails")
    registry.register_job(task_c, name="task_c", queue="billing")
    return registry


def test_list_jobs_contains_registered_names():
    registry = _make_registry_with_jobs()
    names = list(registry.list_jobs().keys())
    assert len(names) == 3
    assert all(isinstance(n, str) for n in names)


def test_list_jobs_can_be_filtered_by_queue():
    registry = _make_registry_with_jobs()
    jobs = registry.list_jobs()
    email_jobs = [name for name, cfg in jobs.items() if cfg.get("queue") == "emails"]
    assert len(email_jobs) == 2
    billing_jobs = [name for name, cfg in jobs.items() if cfg.get("queue") == "billing"]
    assert len(billing_jobs) == 1
    empty = [name for name, cfg in jobs.items() if cfg.get("queue") == "nonexistent"]
    assert empty == []


def test_clear_removes_all_jobs():
    registry = _make_registry_with_jobs()
    assert len(registry) > 0
    registry.clear()
    assert len(registry) == 0
    assert registry.list_jobs() == {}


def test_remove_job_returns_true_for_existing():
    registry = _make_registry_with_jobs()
    names = list(registry.list_jobs().keys())
    assert registry.remove_job(names[0]) is True
    assert len(registry) == 2


def test_remove_job_returns_false_for_missing():
    registry = _make_registry_with_jobs()
    assert registry.remove_job("nonexistent.job") is False


def test_list_jobs_returns_copy_of_configs():
    registry = _make_registry_with_jobs()
    jobs = registry.list_jobs()
    assert isinstance(jobs, dict)
    assert len(jobs) == 3
    # Verify it's a copy, not a reference
    jobs["new_key"] = "value"
    assert "new_key" not in registry.list_jobs()


def test_len_reflects_registered_count():
    registry = JobRegistry()
    assert len(registry) == 0

    async def task():
        pass

    registry.register_job(task, name="task")
    assert len(registry) == 1


def test_contains_checks_job_names():
    registry = JobRegistry()

    async def task():
        pass

    wrapped = registry.register_job(task, name="task")
    job_name = wrapped._soniq_name
    assert job_name in registry
    assert "nonexistent.task" not in registry


def test_register_job_stores_all_config_fields():
    registry = JobRegistry()

    async def my_job():
        pass

    registry.register_job(
        my_job,
        name="my_job",
        retries=5,
        priority=50,
        queue="special",
        unique=True,
        retry_delay=[1, 2, 5],
        retry_backoff=True,
        retry_max_delay=30,
        timeout=60,
    )
    config = registry.get_job("my_job")
    assert config["max_retries"] == 5
    assert config["priority"] == 50
    assert config["queue"] == "special"
    assert config["unique"] is True
    assert config["retry_delay"] == [1, 2, 5]
    assert config["retry_backoff"] is True
    assert config["retry_max_delay"] == 30
    assert config["timeout"] == 60


def test_max_retries_overrides_retries():
    registry = JobRegistry()

    async def my_job():
        pass

    registry.register_job(my_job, name="my_job", retries=3, max_retries=10)
    assert registry.get_job("my_job")["max_retries"] == 10


def test_validate_alias_for_args_model():
    from pydantic import BaseModel

    class MyModel(BaseModel):
        x: int

    registry = JobRegistry()

    async def my_job():
        pass

    registry.register_job(my_job, name="my_job", validate=MyModel)
    assert registry.get_job("my_job")["args_model"] is MyModel


# ---------------------------------------------------------------------------
# Celery-style name= resolution
#
# - Pass `name=` and it's used verbatim (validated against
#   SONIQ_TASK_NAME_PATTERN; bad shapes raise SONIQ_INVALID_TASK_NAME).
# - Omit `name=` and the name is derived as f"{module}.{qualname}",
#   matching Celery / Dramatiq / RQ semantics. The derived name skips
#   pattern validation since the user did not pick it.
# ---------------------------------------------------------------------------


import os  # noqa: E402

import pytest  # noqa: E402

from tests.db_utils import TEST_DATABASE_URL  # noqa: E402

os.environ.setdefault("SONIQ_DATABASE_URL", TEST_DATABASE_URL)

from soniq.errors import SONIQ_INVALID_TASK_NAME, SoniqError  # noqa: E402


class TestNameResolution:
    def test_explicit_name_is_used_as_registry_key(self):
        registry = JobRegistry()

        async def some_func():
            pass

        wrapped = registry.register_job(some_func, name="billing.foo")
        assert "billing.foo" in registry
        assert wrapped._soniq_name == "billing.foo"

    def test_explicit_name_takes_precedence_over_derivation(self):
        registry = JobRegistry()

        async def some_func():
            pass

        registry.register_job(some_func, name="billing.foo")
        derived = f"{some_func.__module__}.{some_func.__name__}"
        # The explicit name wins; the derived name is not also registered.
        assert derived not in registry or derived == "billing.foo"

    def test_missing_name_derives_from_module_qualname(self):
        """Celery-style: register_job() without name= derives the task
        name from f'{module}.{qualname}'."""
        registry = JobRegistry()

        async def some_func():
            pass

        wrapped = registry.register_job(some_func)
        expected = f"{some_func.__module__}.{some_func.__name__}"
        assert wrapped._soniq_name == expected
        assert expected in registry

    def test_empty_name_raises_invalid_task_name(self):
        registry = JobRegistry()

        async def some_func():
            pass

        with pytest.raises(SoniqError) as exc_info:
            registry.register_job(some_func, name="")
        assert exc_info.value.error_code == SONIQ_INVALID_TASK_NAME

    @pytest.mark.parametrize(
        "bad_name",
        ["Bad Name", "Has.Caps", ".leading", "trailing.", "double..dot", "dash-name"],
    )
    def test_bad_explicit_name_format_raises_invalid_task_name(self, bad_name):
        """An explicit name= still has to match SONIQ_TASK_NAME_PATTERN.
        A typo in the protocol identifier is the same hazard the
        pattern was added to catch."""
        registry = JobRegistry()

        async def some_func():
            pass

        with pytest.raises(SoniqError) as exc_info:
            registry.register_job(some_func, name=bad_name)
        assert exc_info.value.error_code == SONIQ_INVALID_TASK_NAME

    def test_derived_name_skips_pattern_validation(self):
        """Module-derived names may legitimately contain camelcase or
        test-class chrome that the default pattern would reject; the
        user did not pick the derived name, so we accept it as-is."""
        registry = JobRegistry()

        async def MixedCaseFunc():  # noqa: N802
            pass

        wrapped = registry.register_job(MixedCaseFunc)
        # Derivation succeeds even though MixedCaseFunc would fail the
        # default lowercase-segments pattern if passed explicitly.
        assert wrapped._soniq_name in registry

    def test_soniq_name_attribute_matches_registry_key(self):
        registry = JobRegistry()

        async def some_func():
            pass

        wrapped = registry.register_job(some_func, name="a.b.c")
        assert wrapped._soniq_name == "a.b.c"
        assert wrapped._soniq_name in registry

    def test_app_job_decorator_with_no_args_derives_name(self):
        """`@app.job` (no parens) registers with a derived name."""
        from soniq import Soniq

        app = Soniq(database_url=TEST_DATABASE_URL)

        @app.job()
        async def my_local_handler():
            pass

        expected = f"{my_local_handler.__module__}.my_local_handler"
        assert my_local_handler._soniq_name == expected
        assert expected in app._job_registry

    def test_app_job_decorator_with_empty_parens_derives_name(self):
        """`@app.job()` (empty parens) registers with a derived name."""
        from soniq import Soniq

        app = Soniq(database_url=TEST_DATABASE_URL)

        @app.job()
        async def my_other_handler():
            pass

        expected = f"{my_other_handler.__module__}.my_other_handler"
        assert expected in app._job_registry

    def test_app_job_decorator_accepts_explicit_name(self):
        from soniq import Soniq

        app = Soniq(database_url=TEST_DATABASE_URL)

        @app.job(name="billing.test.foo")
        async def f():
            pass

        assert f._soniq_name == "billing.test.foo"
        assert "billing.test.foo" in app._job_registry

    def test_module_level_app_job_no_args_derives_name(self):
        """`@app.job` (no parens) derives the name from the function module and name."""
        from soniq import Soniq

        app = Soniq()

        @app.job()
        async def my_handler():
            pass

        expected = f"{my_handler.__module__}.my_handler"
        assert expected in app._job_registry
