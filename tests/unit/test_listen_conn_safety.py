"""
Test that the LISTEN connection in the worker loop is acquired safely.

The listen_handle must be initialized to None before try so the finally
block can always clean it up, even if an exception occurs during setup.
"""

from pathlib import Path


def test_listen_handle_initialized_before_try():
    """
    listen_handle must be set to None before the try block in
    _run_continuous so the finally block can always check it.
    """
    worker_path = Path(__file__).parent.parent.parent / "soniq" / "core" / "worker.py"
    source = worker_path.read_text()

    # Verify listen_handle = None appears before the try block
    lines = source.splitlines()
    found_none_init = False
    found_try = False

    for line in lines:
        stripped = line.strip()
        if "listen_handle = None" in stripped:
            found_none_init = True
        if found_none_init and stripped == "try:":
            found_try = True
            break

    assert found_none_init, "listen_handle should be initialized to None"
    assert found_try, "try block should appear after listen_handle = None"
