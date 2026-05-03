"""
Soniq Exception Classes
"""

from typing import Any, Dict, List, Optional

SONIQ_UNKNOWN_TASK_NAME = "SONIQ_UNKNOWN_TASK_NAME"
SONIQ_INVALID_TASK_NAME = "SONIQ_INVALID_TASK_NAME"
SONIQ_TASK_ARGS_INVALID = "SONIQ_TASK_ARGS_INVALID"
SONIQ_PLUGIN_DUPLICATE = "SONIQ_PLUGIN_DUPLICATE"


class SoniqError(Exception):
    """
    Base exception class for all Soniq errors.

    Provides structured error information and actionable guidance.
    """

    def __init__(
        self,
        message: str,
        error_code: str,
        context: Optional[Dict[str, Any]] = None,
        suggestions: Optional[List[str]] = None,
        documentation_url: Optional[str] = None,
    ):
        self.message = message
        self.error_code = error_code
        self.context = context or {}
        self.suggestions = suggestions or []
        self.documentation_url = documentation_url

        super().__init__(self._format_error_message())

    def _format_error_message(self) -> str:
        """Format a comprehensive error message."""
        lines = [f"Soniq Error [{self.error_code}]: {self.message}"]

        if self.context:
            lines.append("\nContext:")
            for key, value in self.context.items():
                lines.append(f"  {key}: {value}")

        if self.suggestions:
            lines.append("\nSuggestions:")
            for suggestion in self.suggestions:
                lines.append(f"  {suggestion}")

        if self.documentation_url:
            lines.append(f"\nDocumentation: {self.documentation_url}")

        return "\n".join(lines)


class MigrationError(SoniqError):
    """Raised when database migration fails."""

    def __init__(
        self,
        migration_step: str,
        reason: str,
        database_info: Optional[Dict[str, Any]] = None,
    ):
        context = {"migration_step": migration_step, "reason": reason}

        if database_info:
            context.update(database_info)

        super().__init__(
            message=f"Database migration failed at step '{migration_step}': {reason}",
            error_code="MIGRATION_FAILED",
            context=context,
            suggestions=[
                "Check database connectivity and permissions",
                "Verify database user has DDL privileges",
            ],
        )
