"""
Test for the documentation_url formatting path in SoniqError.
"""

from soniq.errors import SoniqError


def test_error_with_documentation_url():
    error = SoniqError(
        message="Setup failed",
        error_code="SETUP_ERROR",
        documentation_url="https://soniq.abhinav.co/errors/setup",
    )
    error_str = str(error)
    assert "Documentation: https://soniq.abhinav.co/errors/setup" in error_str
