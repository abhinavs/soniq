"""
Tests for soniq.core.naming.validate_task_name.
"""

import os

import pytest

from tests.db_utils import TEST_DATABASE_URL

os.environ.setdefault("SONIQ_DATABASE_URL", TEST_DATABASE_URL)

from soniq.core.naming import validate_task_name  # noqa: E402
from soniq.errors import SONIQ_INVALID_TASK_NAME, SoniqError  # noqa: E402
from soniq.settings import SoniqSettings  # noqa: E402

DEFAULT_PATTERN = SoniqSettings().task_name_pattern


class TestValidateTaskName:
    @pytest.mark.parametrize(
        "good",
        [
            "a.b.c",
            "billing.invoices.send.v2",
            "x_y.z_w",
            "foo",
            "billing_v2.send",
            "a.b",
        ],
    )
    def test_accepts_valid_names(self, good):
        assert validate_task_name(good, DEFAULT_PATTERN) == good

    @pytest.mark.parametrize(
        "bad",
        [
            "Billing.x",
            ".leading",
            "trailing.",
            "double..dot",
            "has space",
            "dash-name",
            "",
        ],
    )
    def test_rejects_invalid_names(self, bad):
        with pytest.raises(SoniqError) as exc_info:
            validate_task_name(bad, DEFAULT_PATTERN)
        assert exc_info.value.error_code == SONIQ_INVALID_TASK_NAME

    def test_error_context_carries_name_and_pattern(self):
        with pytest.raises(SoniqError) as exc_info:
            validate_task_name("Bad Name", DEFAULT_PATTERN)
        assert exc_info.value.context["name"] == "Bad Name"
        assert "pattern" in exc_info.value.context

    def test_non_string_input_raises(self):
        with pytest.raises(SoniqError) as exc_info:
            validate_task_name(123, DEFAULT_PATTERN)  # type: ignore[arg-type]
        assert exc_info.value.error_code == SONIQ_INVALID_TASK_NAME
        assert exc_info.value.context["received_type"] == "int"

    def test_pattern_can_be_overridden_via_arg(self):
        assert validate_task_name("Has Space", r".+") == "Has Space"


class TestPatternConfigurable:
    """Override SONIQ_TASK_NAME_PATTERN via env and verify a fresh
    SoniqSettings() reads it. The helper itself is now stateless wrt
    settings (per instance-boundary contract); the configurability lives
    in the caller threading the pattern in."""

    def setup_method(self):
        self.original_env = {
            k: v for k, v in os.environ.items() if k.startswith("SONIQ_")
        }
        for key in list(os.environ.keys()):
            if key.startswith("SONIQ_"):
                del os.environ[key]
        os.environ["SONIQ_DATABASE_URL"] = TEST_DATABASE_URL

    def teardown_method(self):
        for key in list(os.environ.keys()):
            if key.startswith("SONIQ_"):
                del os.environ[key]
        for key, value in self.original_env.items():
            os.environ[key] = value

    def test_permissive_pattern_accepts_previously_invalid(self):
        os.environ["SONIQ_TASK_NAME_PATTERN"] = r"^.+$"
        pattern = SoniqSettings().task_name_pattern
        assert validate_task_name("Has Space", pattern) == "Has Space"
        assert validate_task_name("Billing.X", pattern) == "Billing.X"
