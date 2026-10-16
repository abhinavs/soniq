"""Tests for ``soniq migrations export``."""

import os
import subprocess
import sys
import textwrap

import pytest

from soniq.cli.main import main
from tests.db_utils import make_test_db_url, run_createdb, run_dropdb


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


def test_alembic_revision_applies_and_setup_is_noop(tmp_path):
    pytest.importorskip("alembic")
    pytest.importorskip("psycopg")
    from alembic import command
    from alembic.config import Config

    name = "soniq_alembic_export_test"
    run_dropdb(name)
    run_createdb(name, check=True)
    try:
        url = make_test_db_url(name)
        sa_url = url.replace("postgresql://", "postgresql+psycopg://", 1)

        (tmp_path / "versions").mkdir()
        (tmp_path / "script.py.mako").write_text(
            '"""${message}"""\nrevision = ${repr(up_revision)}\n'
            "down_revision = ${repr(down_revision)}\n"
        )
        (tmp_path / "env.py").write_text(
            textwrap.dedent(
                f"""
                from alembic import context
                from sqlalchemy import create_engine

                with create_engine({sa_url!r}).connect() as conn:
                    context.configure(connection=conn)
                    with context.begin_transaction():
                        context.run_migrations()
                    conn.commit()
                """
            )
        )
        (tmp_path / "versions" / "soniq_0005.py").write_text(_export("--alembic"))

        cfg = Config()
        cfg.set_main_option("script_location", str(tmp_path))
        command.upgrade(cfg, "head")

        env = {**os.environ, "SONIQ_DATABASE_URL": url}
        status = _soniq("migrate-status", env=env)
        assert "Pending migrations: 0" in status.stdout, status.stdout
        setup = _soniq("setup", env=env)
        assert "already up to date" in setup.stdout, setup.stdout
    finally:
        run_dropdb(name)
