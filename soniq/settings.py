"""
Soniq Configuration Management

Uses Pydantic v2 BaseSettings for robust, type-safe configuration with support for
environment variables, config files, and validation.
"""

from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import Field, ValidationError, field_validator
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic_settings.sources import EnvSettingsSource, PydanticBaseSettingsSource


class CustomEnvSource(EnvSettingsSource):
    """Custom environment source that handles comma-separated lists without JSON parsing."""

    def prepare_field_value(
        self, field_name: str, field: FieldInfo, value: Any, value_is_complex: bool
    ) -> Any:
        """Override to handle comma-separated lists for specific fields."""
        if field_name == "queues" and isinstance(value, str):
            # Handle comma-separated queues without JSON parsing
            return [q.strip() for q in value.split(",") if q.strip()]
        return super().prepare_field_value(field_name, field, value, value_is_complex)


class SoniqSettings(BaseSettings):
    """
    Soniq configuration with environment variable support and validation.

    Configuration priority:
    1. Environment variables (SONIQ_*)
    2. Config file (if specified)
    3. Default values
    """

    model_config = SettingsConfigDict(
        env_prefix="SONIQ_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Use custom environment source."""
        return (
            init_settings,
            CustomEnvSource(settings_cls),
            dotenv_settings,
            file_secret_settings,
        )

    # Database Configuration
    database_url: str = Field(
        default="postgresql://postgres@localhost/soniq",
        description="PostgreSQL database URL for Soniq",
    )

    # Job Discovery
    jobs_modules: str = Field(
        default="",
        description=(
            "Comma-separated list of modules to import on worker startup "
            "(e.g. 'my_app.tasks,my_app.other_tasks')"
        ),
    )

    # Worker Configuration
    concurrency: int = Field(
        default=4, ge=1, le=100, description="Default number of concurrent workers"
    )

    queues: List[str] = Field(
        default=["default"],
        description="Default queues to process (when not using dynamic discovery)",
    )

    # Job Processing Settings
    max_retries: int = Field(
        default=3,
        ge=0,
        le=10,
        description="Default maximum retry attempts for failed jobs",
    )

    priority: int = Field(
        default=100,
        ge=1,
        le=1000,
        description="Default job priority (lower = higher priority)",
    )

    enqueue_validation: Literal["strict", "warn", "none"] = Field(
        default="strict",
        description=(
            "How enqueue() handles a string name that is not registered locally. "
            "'strict' raises SONIQ_UNKNOWN_TASK_NAME (default; loud at the call site). "
            "'warn' emits a rate-limited WARN and proceeds (for producer services that "
            "cannot validate locally). 'none' is silent. Does not apply when enqueue() "
            "is called with a TaskRef; that path validates against args_model."
        ),
    )

    task_name_pattern: str = Field(
        default=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$",
        description=(
            "Regex enforced on every task name at @app.job(name=) registration and "
            "at enqueue() call time. Default rejects spaces, capitalisation, leading "
            "dots. Override for teams with existing conventions."
        ),
    )

    producer_id: str = Field(
        default="auto",
        description=(
            "Identifier stamped on every row this instance enqueues, for "
            "observability ('who enqueued this poison message?'). 'auto' "
            "resolves to <hostname>:<pid>:<argv0> the first time a producer_id "
            "is needed. Set explicitly (e.g. 'billing-api') for cleaner "
            "dashboards in multi-deployment topologies."
        ),
    )

    route_map: Dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Consumer-side prefix-to-queue routing. When a job is registered "
            "without an explicit queue=, the longest matching prefix in this "
            "dict determines the queue the consumer's worker polls for that "
            "name. Producer queue= overrides ride on top of the row, not on "
            "this map (the producer is unaware of consumer routing - this is "
            "consumer-side only). Example: "
            "{'billing.': 'billing-queue', 'reports.': 'reports-queue'}."
        ),
    )

    # Timeouts and Intervals
    heartbeat_interval: float = Field(
        default=5.0,
        ge=0.1,
        le=60.0,
        description="Worker heartbeat check interval in seconds",
    )

    job_timeout: Optional[float] = Field(
        default=300.0,
        ge=1.0,
        description="Default job execution timeout in seconds (None or 0 to disable)",
    )

    # Worker Processing Intervals
    cleanup_interval: float = Field(
        default=300.0,
        ge=10.0,
        le=3600.0,
        description="Interval for cleaning up expired jobs and stale workers (seconds)",
    )

    heartbeat_timeout: float = Field(
        default=300.0,
        ge=60.0,
        le=7200.0,
        description="Time after which workers are considered stale (seconds)",
    )

    poll_interval: float = Field(
        default=5.0,
        ge=0.1,
        le=60.0,
        description="Timeout when waiting for job notifications (seconds)",
    )

    error_retry_delay: float = Field(
        default=5.0,
        ge=0.1,
        le=300.0,
        description="Delay before retrying after worker errors (seconds)",
    )

    @field_validator("job_timeout", mode="before")
    @classmethod
    def parse_job_timeout(cls, v: Any) -> Any:
        """Parse job timeout value, converting 0 to None (no timeout)."""
        if v == "0" or v == 0:
            return None
        return v

    # Bounded executor for sync handlers. Default 8 = sane multi-core
    # ceiling for IO-bound sync work; raise for CPU-bound or block-heavy
    # handlers, lower if you want tighter back-pressure under burst load.
    sync_handler_pool_size: int = Field(
        default=8,
        ge=1,
        le=256,
        description=(
            "Max concurrent sync handler threads per Soniq instance. The "
            "post-claim semaphore enforces this bound; jobs claimed beyond "
            "the bound wait in `processing` state until a slot frees."
        ),
    )

    # Bounded async-handler shutdown wall time. Sync handlers are not
    # bounded by this alone (see sync_handler_grace_seconds and the shutdown
    # contract in docs/_internals/contracts/shutdown.md).
    shutdown_timeout: float = Field(
        default=30.0,
        ge=0.1,
        le=3600.0,
        description=(
            "Wall time (seconds) the worker waits for in-flight jobs to "
            "drain after SIGTERM. On expiry the async branch cancels + "
            "nacks; the sync branch enters its own grace window."
        ),
    )

    sync_handler_grace_seconds: Optional[float] = Field(
        default=None,
        ge=0.0,
        description=(
            "Flat additional grace (seconds) the worker waits for an "
            "in-flight sync handler thread once shutdown_timeout expires. "
            "None means use job_timeout. After grace the worker keeps "
            "waiting until the thread returns or the orchestrator sends "
            "SIGKILL; sync shutdown is unbounded by Soniq alone."
        ),
    )

    # Connection Pool Settings
    pool_min_size: int = Field(
        default=5, ge=1, le=100, description="Minimum database connection pool size"
    )

    pool_max_size: int = Field(
        default=20, ge=1, le=200, description="Maximum database connection pool size"
    )

    pool_headroom: int = Field(
        default=2,
        ge=0,
        le=50,
        description="Extra connections reserved beyond worker concurrency (listener/heartbeat)",
    )

    # Logging Configuration
    log_level: str = Field(
        default="INFO",
        description="Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)",
    )

    # Job Result Retention (TTL)
    result_ttl: int = Field(
        default=300,
        ge=0,
        description="Time to live for completed jobs in seconds (0=delete immediately, default=300 for 5 minutes)",
    )

    # Snooze
    snooze_max_seconds: float = Field(
        default=24 * 3600,
        gt=0,
        description="Upper bound on Snooze(seconds=...) before the value is capped, preventing handlers from scheduling jobs arbitrarily far in the future",
    )

    # Development/Testing
    debug: bool = Field(default=False, description="Enable debug mode")

    @field_validator("debug", mode="before")
    @classmethod
    def parse_debug(cls, v: Any) -> Any:
        """Parse debug value from environment variables, handling empty strings."""
        if isinstance(v, str):
            if v.lower() in ("", "0", "false", "f", "no", "n"):
                return False
            elif v.lower() in ("1", "true", "t", "yes", "y"):
                return True
        return v

    environment: str = Field(
        default="production",
        description="Environment name (development, testing, production)",
    )

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if v.upper() not in valid_levels:
            raise ValueError(f"log_level must be one of {valid_levels}")
        return v.upper()

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, v: str) -> str:
        if not v:
            raise ValueError("database_url must not be empty")
        return v


# Global settings instance
_settings: Optional[SoniqSettings] = None


def get_settings(
    config_file: Optional[Path] = None, reload: bool = False
) -> SoniqSettings:
    """
    Get Soniq settings with caching.

    Args:
        config_file: Optional path to config file
        reload: Force reload settings from environment

    Returns:
        SoniqSettings instance
    """
    global _settings

    if _settings is None or reload:
        try:
            if config_file and config_file.exists():
                # Load settings from file if provided
                _settings = SoniqSettings(_env_file=str(config_file))  # type: ignore[call-arg]
            else:
                # Load from environment variables
                _settings = SoniqSettings()

        except ValidationError as e:
            raise ValueError(f"Invalid Soniq configuration: {e}")

    return _settings


def reload_settings(config_file: Optional[Path] = None) -> SoniqSettings:
    """Force reload settings from environment/config file."""
    return get_settings(config_file=config_file, reload=True)


def configure(**kwargs: Any) -> SoniqSettings:
    """
    Configure Soniq settings programmatically.

    Creates a new settings instance directly with kwargs.
    Does not modify os.environ.
    """
    global _settings

    if not kwargs:
        return get_settings()

    unknown = [key for key in kwargs if key not in SoniqSettings.model_fields]
    if unknown:
        raise ValueError(f"Unknown Soniq settings: {', '.join(unknown)}")

    _settings = SoniqSettings(**kwargs)
    return _settings


def __getattr__(name: str) -> Any:
    """Expose SONIQ_* settings as module attributes for CLI convenience."""
    if name.startswith("SONIQ_"):
        key = name[len("SONIQ_") :].lower()
        current = get_settings()
        if hasattr(current, key):
            return getattr(current, key)
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
