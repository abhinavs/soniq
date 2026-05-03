"""
Comprehensive tests for Soniq's Pydantic-based configuration system.

Tests cover:
- Default configuration loading
- Environment variable overrides
- Configuration validation
- Explicit SONIQ_DATABASE_URL requirement (no DATABASE_URL fallback)
- Pydantic availability fallback
- Configuration file loading
- get_settings() and reload_settings() functions
"""

import os
import tempfile
from pathlib import Path

import pytest

from tests.db_utils import TEST_DATABASE_URL

# Set test database before importing soniq modules
os.environ["SONIQ_DATABASE_URL"] = TEST_DATABASE_URL


class TestPydanticConfiguration:
    """Test the Pydantic-based configuration system."""

    def setup_method(self):
        """Set up each test with clean environment."""
        # Store original environment
        self.original_env = {}
        for key in os.environ:
            if key.startswith("SONIQ_"):
                self.original_env[key] = os.environ[key]

        # Clear Soniq environment variables for clean testing
        for key in list(os.environ.keys()):
            if key.startswith("SONIQ_"):
                del os.environ[key]

        # Clear settings cache
        import soniq.settings

        soniq.settings._settings = None

    def teardown_method(self):
        """Clean up after each test."""
        # Restore original environment
        for key in list(os.environ.keys()):
            if key.startswith("SONIQ_"):
                del os.environ[key]

        for key, value in self.original_env.items():
            os.environ[key] = value

        # Clear settings cache
        import soniq.settings

        soniq.settings._settings = None

    def test_default_configuration_loading(self):
        """Test that configuration loads correctly with default values."""
        from soniq.settings import get_settings

        settings = get_settings()

        # Test default values
        assert settings.database_url == "postgresql://postgres@localhost/soniq"
        assert settings.jobs_modules == ""
        assert settings.concurrency == 4
        assert settings.queues == ["default"]
        assert settings.max_retries == 3
        assert settings.priority == 100
        assert settings.heartbeat_interval == 5.0
        assert settings.job_timeout == 300.0
        assert settings.pool_min_size == 5
        assert settings.pool_max_size == 20
        assert settings.pool_headroom == 2
        assert settings.log_level == "INFO"
        assert settings.debug is False
        assert settings.environment == "production"

    def test_environment_variable_overrides(self):
        """Test environment variable override functionality."""
        # Set environment variables
        os.environ["SONIQ_DATABASE_URL"] = "postgresql://test@localhost/test_db"
        os.environ["SONIQ_JOBS_MODULES"] = "myapp.tasks,myapp.other_tasks"
        os.environ["SONIQ_CONCURRENCY"] = "8"
        os.environ["SONIQ_QUEUES"] = "urgent,default,low"
        os.environ["SONIQ_MAX_RETRIES"] = "5"
        os.environ["SONIQ_PRIORITY"] = "200"
        os.environ["SONIQ_HEARTBEAT_INTERVAL"] = "10.0"
        os.environ["SONIQ_JOB_TIMEOUT"] = "300.0"
        os.environ["SONIQ_POOL_MIN_SIZE"] = "10"
        os.environ["SONIQ_POOL_MAX_SIZE"] = "50"
        os.environ["SONIQ_POOL_HEADROOM"] = "4"
        os.environ["SONIQ_LOG_LEVEL"] = "DEBUG"
        os.environ["SONIQ_DEBUG"] = "true"
        os.environ["SONIQ_ENVIRONMENT"] = "development"

        from soniq.settings import get_settings

        settings = get_settings(reload=True)

        # Test overridden values
        assert settings.database_url == "postgresql://test@localhost/test_db"
        assert settings.jobs_modules == "myapp.tasks,myapp.other_tasks"
        assert settings.concurrency == 8
        assert settings.queues == ["urgent", "default", "low"]
        assert settings.max_retries == 5
        assert settings.priority == 200
        assert settings.heartbeat_interval == 10.0
        assert settings.job_timeout == 300.0
        assert settings.pool_min_size == 10
        assert settings.pool_max_size == 50
        assert settings.pool_headroom == 4
        assert settings.log_level == "DEBUG"
        assert settings.debug is True
        assert settings.environment == "development"

    def test_configuration_validation_empty_database_url(self):
        """Test configuration validation rejects empty database URL."""
        os.environ["SONIQ_DATABASE_URL"] = ""

        from soniq.settings import get_settings

        with pytest.raises(
            ValueError,
            match=r"(?s)Invalid Soniq configuration.*database_url must not be empty",
        ):
            get_settings(reload=True)

    def test_configuration_validation_invalid_log_level(self):
        """Test configuration validation for invalid log levels."""
        os.environ["SONIQ_LOG_LEVEL"] = "INVALID"

        from soniq.settings import get_settings

        # Should raise validation error for invalid log level
        with pytest.raises(
            ValueError,
            match=r"(?s)Invalid Soniq configuration.*log_level must be one of",
        ):
            get_settings(reload=True)

    def test_configuration_validation_numeric_ranges(self):
        """Test configuration validation for numeric range constraints."""
        # Test concurrency out of range
        os.environ["SONIQ_CONCURRENCY"] = "200"  # Max is 100

        from soniq.settings import get_settings

        with pytest.raises(ValueError, match=r"(?s)Invalid Soniq configuration"):
            get_settings(reload=True)

        # Reset and test minimum
        del os.environ["SONIQ_CONCURRENCY"]
        os.environ["SONIQ_MAX_RETRIES"] = "-1"  # Min is 0

        with pytest.raises(ValueError, match=r"(?s)Invalid Soniq configuration"):
            get_settings(reload=True)

    def test_database_url_is_not_used_as_fallback(self):
        """Test that DATABASE_URL is NOT used as fallback - only SONIQ_DATABASE_URL."""
        # Set DATABASE_URL but not SONIQ_DATABASE_URL
        os.environ["DATABASE_URL"] = (
            "postgresql://should_not_be_used@localhost/legacy_db"
        )

        # Remove SONIQ_DATABASE_URL if it exists
        if "SONIQ_DATABASE_URL" in os.environ:
            del os.environ["SONIQ_DATABASE_URL"]

        from soniq.settings import get_settings

        settings = get_settings(reload=True)

        # Should use default value, NOT the DATABASE_URL fallback
        assert settings.database_url == "postgresql://postgres@localhost/soniq"
        assert (
            settings.database_url
            != "postgresql://should_not_be_used@localhost/legacy_db"
        )

    def test_soniq_database_url_is_used_when_set(self):
        """Test that SONIQ_DATABASE_URL is properly used when set."""
        # Set both DATABASE_URL and SONIQ_DATABASE_URL
        os.environ["DATABASE_URL"] = "postgresql://ignored@localhost/ignored_db"
        os.environ["SONIQ_DATABASE_URL"] = "postgresql://soniq_user@localhost/soniq_db"

        from soniq.settings import get_settings

        settings = get_settings(reload=True)

        # Only SONIQ_DATABASE_URL should be used (DATABASE_URL ignored)
        assert settings.database_url == "postgresql://soniq_user@localhost/soniq_db"

    def test_configuration_file_loading(self):
        """Test configuration file loading functionality."""
        # Create temporary config file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
            f.write("SONIQ_DATABASE_URL=postgresql://config@localhost/config_db\n")
            f.write("SONIQ_CONCURRENCY=12\n")
            f.write("SONIQ_LOG_LEVEL=WARNING\n")
            config_file = Path(f.name)

        try:
            from soniq.settings import get_settings

            settings = get_settings(config_file=config_file, reload=True)

            # Should load from config file
            assert settings.database_url == "postgresql://config@localhost/config_db"
            assert settings.concurrency == 12
            assert settings.log_level == "WARNING"

        finally:
            # Clean up
            config_file.unlink()

    def test_get_settings_caching(self):
        """Test that get_settings() properly caches settings."""
        from soniq.settings import get_settings

        # First call
        settings1 = get_settings()

        # Second call should return same instance (cached)
        settings2 = get_settings()

        assert settings1 is settings2

    def test_get_settings_reload_functionality(self):
        """Test get_settings() reload functionality."""
        # Initial settings
        os.environ["SONIQ_DATABASE_URL"] = "postgresql://initial@localhost/initial_db"

        from soniq.settings import get_settings

        settings1 = get_settings(reload=True)
        assert settings1.database_url == "postgresql://initial@localhost/initial_db"

        # Change environment and reload
        os.environ["SONIQ_DATABASE_URL"] = "postgresql://reloaded@localhost/reloaded_db"
        settings2 = get_settings(reload=True)

        # Should get new settings
        assert settings2.database_url == "postgresql://reloaded@localhost/reloaded_db"
        assert settings1 is not settings2  # Different instances after reload

    def test_reload_settings_function(self):
        """Test the standalone reload_settings() function."""
        from soniq.settings import reload_settings

        # Set initial environment
        os.environ["SONIQ_DATABASE_URL"] = "postgresql://before@localhost/before_db"

        settings1 = reload_settings()
        assert settings1.database_url == "postgresql://before@localhost/before_db"

        # Change environment
        os.environ["SONIQ_DATABASE_URL"] = "postgresql://after@localhost/after_db"

        settings2 = reload_settings()
        assert settings2.database_url == "postgresql://after@localhost/after_db"

    def test_settings_access_via_get_settings(self):
        """Test that settings are accessed via get_settings() function."""
        os.environ["SONIQ_DATABASE_URL"] = (
            "postgresql://export_test@localhost/export_db"
        )

        # Reload settings to pick up environment variables
        from soniq.settings import get_settings

        settings = get_settings(reload=True)

        # Settings should be accessible via the settings object
        assert settings.database_url == "postgresql://export_test@localhost/export_db"

    def test_case_insensitive_configuration(self):
        """Test case-insensitive configuration handling."""
        # Set case variations
        os.environ["soniq_database_url"] = (
            "postgresql://case@localhost/case_db"  # lowercase
        )
        os.environ["SONIQ_LOG_LEVEL"] = "debug"  # lowercase value should be normalized

        from soniq.settings import get_settings

        settings = get_settings(reload=True)

        # Should handle case variations properly
        assert settings.database_url == "postgresql://case@localhost/case_db"
        assert settings.log_level == "DEBUG"  # Should be normalized to uppercase

    def test_list_configuration_parsing(self):
        """Test parsing of comma-separated list configurations."""
        os.environ["SONIQ_QUEUES"] = "high,normal,low,bulk"

        from soniq.settings import get_settings

        settings = get_settings(reload=True)

        # Should parse comma-separated values into list
        assert settings.queues == ["high", "normal", "low", "bulk"]

    def test_boolean_configuration_parsing(self):
        """Test parsing of boolean configurations from strings."""
        # Test various boolean representations
        test_cases = [
            ("true", True),
            ("True", True),
            ("TRUE", True),
            ("1", True),
            ("false", False),
            ("False", False),
            ("FALSE", False),
            ("0", False),
            ("", False),
        ]

        for env_value, expected in test_cases:
            os.environ["SONIQ_DEBUG"] = env_value

            from soniq.settings import get_settings

            settings = get_settings(reload=True)

            assert (
                settings.debug == expected
            ), f"Failed for env_value='{env_value}', expected={expected}, got={settings.debug}"

    def test_numeric_configuration_validation(self):
        """Test numeric configuration validation and type conversion."""
        os.environ["SONIQ_CONCURRENCY"] = "8"
        os.environ["SONIQ_HEARTBEAT_INTERVAL"] = "2.5"
        os.environ["SONIQ_JOB_TIMEOUT"] = "600.0"

        from soniq.settings import get_settings

        settings = get_settings(reload=True)

        # Should properly convert string values to correct types
        assert isinstance(settings.concurrency, int)
        assert settings.concurrency == 8
        assert isinstance(settings.heartbeat_interval, float)
        assert settings.heartbeat_interval == 2.5
        assert isinstance(settings.job_timeout, float)
        assert settings.job_timeout == 600.0

    def test_optional_timeout_handling(self):
        """Test handling of optional timeout configurations."""
        # Test with explicit zero (should become None)
        os.environ["SONIQ_JOB_TIMEOUT"] = "0"

        from soniq.settings import get_settings

        settings = get_settings(reload=True)

        # Zero timeout should be converted to None in fallback mode
        # (This behavior depends on the implementation)
        assert settings.job_timeout is None or settings.job_timeout == 0.0

        # Test with valid timeout
        os.environ["SONIQ_JOB_TIMEOUT"] = "300"
        settings = get_settings(reload=True)

        assert settings.job_timeout == 300.0


class TestCrossServiceSettings:
    """enqueue_validation and task_name_pattern (cross-service work)."""

    def setup_method(self):
        self.original_env = {
            k: v for k, v in os.environ.items() if k.startswith("SONIQ_")
        }
        for key in list(os.environ.keys()):
            if key.startswith("SONIQ_"):
                del os.environ[key]
        os.environ["SONIQ_DATABASE_URL"] = TEST_DATABASE_URL
        import soniq.settings

        soniq.settings._settings = None

    def teardown_method(self):
        for key in list(os.environ.keys()):
            if key.startswith("SONIQ_"):
                del os.environ[key]
        for key, value in self.original_env.items():
            os.environ[key] = value
        import soniq.settings

        soniq.settings._settings = None

    def test_enqueue_validation_default_is_strict(self):
        from soniq.settings import get_settings

        settings = get_settings(reload=True)
        assert settings.enqueue_validation == "strict"

    @pytest.mark.parametrize("mode", ["strict", "warn", "none"])
    def test_enqueue_validation_accepts_each_literal(self, mode):
        os.environ["SONIQ_ENQUEUE_VALIDATION"] = mode
        from soniq.settings import get_settings

        settings = get_settings(reload=True)
        assert settings.enqueue_validation == mode

    def test_enqueue_validation_rejects_unknown_value(self):
        os.environ["SONIQ_ENQUEUE_VALIDATION"] = "loose"
        from soniq.settings import get_settings

        with pytest.raises(
            ValueError, match=r"(?s)Invalid Soniq configuration.*enqueue_validation"
        ):
            get_settings(reload=True)

    def test_enqueue_validation_env_override(self):
        os.environ["SONIQ_ENQUEUE_VALIDATION"] = "warn"
        from soniq.settings import get_settings

        settings = get_settings(reload=True)
        assert settings.enqueue_validation == "warn"

    def test_task_name_pattern_default_accepts_canonical_names(self):
        import re

        from soniq.settings import get_settings

        settings = get_settings(reload=True)
        regex = re.compile(settings.task_name_pattern)
        for good in [
            "billing.invoices.send.v2",
            "a.b.c",
            "x_y.z_w",
            "foo",
            "billing_v2.send",
        ]:
            assert regex.fullmatch(good), f"expected {good!r} to match"

    def test_task_name_pattern_default_rejects_bad_names(self):
        import re

        from soniq.settings import get_settings

        settings = get_settings(reload=True)
        regex = re.compile(settings.task_name_pattern)
        for bad in [
            " Billing.X",
            ".leading",
            "trailing.",
            "double..dot",
            "has space",
            "dash-name",
            "",
            "Billing.x",
        ]:
            assert not regex.fullmatch(bad), f"expected {bad!r} to fail"

    def test_task_name_pattern_can_be_overridden(self):
        os.environ["SONIQ_TASK_NAME_PATTERN"] = r"^.+$"
        from soniq.settings import get_settings

        settings = get_settings(reload=True)
        assert settings.task_name_pattern == r"^.+$"
