"""Static type checks for ``@soniq.job()`` handles, run with ``mypy --strict``.

Each snippet is written to a temp file and checked. Lines marked ``# E`` must
produce an error; no other line may.
"""

import importlib.util
import os
import re
import subprocess
import sys
import textwrap

import pytest

import soniq

# Editable installs use an import hook that mypy cannot follow, so point
# mypy at the directory that holds the imported package.
SONIQ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(soniq.__file__)))

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("mypy") is None, reason="mypy is not installed"
)

HEADER = """\
import asyncio
from typing import Any, Awaitable, Callable, ParamSpec, TypeVar

from soniq import Soniq

P = ParamSpec("P")
R = TypeVar("R")

soniq = Soniq(backend="memory")


@soniq.job(name="users.send_welcome")
async def send_welcome(to: str, retries: int = 0) -> dict[str, str]:
    return {"to": to}

"""

NEW_STYLE = """
async def main() -> None:
    job_id: str = await send_welcome.enqueue(to="dev@example.com")
    await send_welcome.enqueue(to="dev@example.com", retries=2)
    await send_welcome.options(queue="emails", priority=1).enqueue(to="x")
    result: dict[str, str] = await send_welcome(to="x")


asyncio.run(send_welcome.enqueue(to="dev@example.com"))
"""

# Code written against 0.0.5. It must type-check unchanged.
OLD_STYLE = """
def logged(fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    async def inner(*args: P.args, **kwargs: P.kwargs) -> R:
        return await fn(*args, **kwargs)

    return inner


async def main() -> None:
    await soniq.enqueue(send_welcome, to="dev@example.com")
    await soniq.enqueue("users.send_welcome", args={"to": "x"})
    await send_welcome("x")
    await logged(send_welcome)(to="x")
    task = asyncio.create_task(send_welcome(to="x"))
    await task
    name: str = send_welcome._soniq_name
    config: dict[str, Any] = send_welcome._soniq_config
    label: str = send_welcome.__name__


asyncio.run(send_welcome(to="x"))
"""

BAD = """
async def main() -> None:
    await send_welcome.enqueue(too="x")  # E
    await send_welcome.enqueue(to=1)  # E
    await send_welcome.enqueue()  # E
    await send_welcome.options(queue="emails").enqueue(too="x")  # E
    await send_welcome.options(queues="emails")  # E
    n: int = await send_welcome.enqueue(to="x")  # E
"""


def _mypy_errors(tmp_path, body: str) -> dict[int, str]:
    source = HEADER + textwrap.dedent(body)
    path = tmp_path / "snippet.py"
    path.write_text(source)
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--strict",
            "--no-error-summary",
            "--follow-imports=silent",
            "--config-file=",
            "--cache-dir",
            str(tmp_path / ".mypy_cache"),
            str(path),
        ],
        env={**os.environ, "MYPYPATH": SONIQ_ROOT},
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    errors: dict[int, str] = {}
    for line in proc.stdout.splitlines():
        m = re.match(r".*snippet\.py:(\d+): error: (.*)", line)
        if m:
            errors[int(m.group(1))] = m.group(2)
    if proc.returncode not in (0, 1) or (proc.returncode == 1 and not errors):
        pytest.fail(f"mypy failed to run:\n{proc.stdout}\n{proc.stderr}")
    return errors


def _expected_lines(body: str) -> set[int]:
    source = HEADER + textwrap.dedent(body)
    return {i for i, line in enumerate(source.splitlines(), 1) if "# E" in line}


def test_new_style_passes(tmp_path):
    assert _mypy_errors(tmp_path, NEW_STYLE) == {}


def test_0_0_5_style_passes(tmp_path):
    assert _mypy_errors(tmp_path, OLD_STYLE) == {}


def test_wrong_arguments_fail(tmp_path):
    errors = _mypy_errors(tmp_path, BAD)
    assert set(errors) == _expected_lines(BAD), errors
