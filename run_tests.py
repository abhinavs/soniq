#!/usr/bin/env python3
"""
Soniq Test Runner

Self-sufficient wrapper around pytest: bootstraps a .venv with dev deps,
runs flake8, then runs the whole test suite in one pytest session.

Extra args are passed through to pytest, e.g.:
    python run_tests.py -k dead_letter -x
"""
import os
import subprocess
import sys


def _bool_env(name: str) -> bool:
    value = os.environ.get(name, "")
    return value.lower() in {"1", "true", "yes", "on"}


def _venv_python_path(venv_dir: str) -> str:
    return os.path.join(venv_dir, "bin", "python")


def _bootstrap_venv(project_root: str) -> None:
    """Ensure a venv exists, dependencies are installed, and re-exec in the venv."""
    if _bool_env("SONIQ_TEST_VENV_BOOTSTRAPPED"):
        return

    venv_dir = os.path.join(project_root, ".venv")
    venv_python = _venv_python_path(venv_dir)

    if not os.path.exists(venv_python):
        subprocess.check_call([sys.executable, "-m", "venv", venv_dir])

    try:
        subprocess.check_call([venv_python, "-m", "pip", "install", "--upgrade", "pip"])
    except Exception as exc:  # noqa: PIE786
        print(
            "⚠️ pip upgrade failed inside test venv; continuing with the existing pip installation."
        )
        print(f"   Details: {exc}")
    # Editable install failures must abort: tests run against a stale venv
    # are misleading-green and were the symptom that triggered this guard.
    subprocess.check_call(
        [venv_python, "-m", "pip", "install", "-e", ".[dev]"],
        cwd=project_root,
    )

    env = os.environ.copy()
    env["SONIQ_TEST_VENV_BOOTSTRAPPED"] = "1"
    env["SONIQ_TEST_VENV_PYTHON"] = venv_python
    os.execvpe(venv_python, [venv_python, __file__] + sys.argv[1:], env)


def _python() -> str:
    return os.environ.get("SONIQ_TEST_VENV_PYTHON", sys.executable)


def run_flake8() -> None:
    """Run flake8 linting; abort the run on failure."""
    print("\n" + "=" * 60)
    print("Running flake8 lint checks")
    print("=" * 60)
    result = subprocess.run([_python(), "-m", "flake8", "soniq"])
    if result.returncode != 0:
        sys.exit(result.returncode)


def run_pytest() -> int:
    """Run the whole suite in one pytest session. Returns the exit code."""
    print("\n" + "=" * 60)
    print("Running pytest (full suite)")
    print("=" * 60)
    return subprocess.run(
        [_python(), "-m", "pytest", "tests/", "-q"] + sys.argv[1:]
    ).returncode


def main() -> int:
    print("🚀 Soniq Test Suite")

    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)
    print(f"📁 Running tests from: {script_dir}")

    _bootstrap_venv(script_dir)
    run_flake8()

    # Avoid row locks during tests when requested
    os.environ.setdefault("SONIQ_SKIP_UPDATE_LOCK", "true")

    code = run_pytest()
    print(
        "\n🎉 ALL TESTS PASSING!"
        if code == 0
        else "\n⚠️  Tests failed. See output above."
    )
    return code


if __name__ == "__main__":
    sys.exit(main())
