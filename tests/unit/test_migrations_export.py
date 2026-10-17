"""Tests for ``soniq migrations export``."""

import subprocess
import sys

from soniq.cli.main import main


def _soniq(*args, env=None, check=True):
    return subprocess.run(
        [sys.executable, "-m", "soniq.cli.main", *args],
        capture_output=True,
        text=True,
        check=check,
        env=env,
    )


def _export(*args):
    return _soniq("migrations", "export", *args).stdout


def test_sql_output_has_all_core_files():
    out = _export("--sql")
    assert "CREATE TABLE" in out
    assert "-- soniq migration 0001_core" in out


def test_from_version_filters_older():
    out = _export("--sql", "--from", "0003")
    assert "0001_core" not in out
    assert "0004_webhooks" in out


def test_from_latest_exports_nothing():
    assert main(["migrations", "export", "--sql", "--from", "9999"]) == 1


def test_alembic_output_is_valid_python():
    src = _export("--alembic", "--down-revision", "abc123")
    compile(src, "revision.py", "exec")
    assert "down_revision = 'abc123'" in src
    assert "revision = 'soniq_0005'" in src
