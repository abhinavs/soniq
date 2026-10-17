"""``soniq migrations export --alembic`` against a real database."""

import os
import subprocess
import sys
import textwrap

import pytest

from tests.db_utils import make_test_db_url, run_createdb, run_dropdb


def _soniq(*args, env=None):
    return subprocess.run(
        [sys.executable, "-m", "soniq.cli.main", *args],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )


def _export(*args):
    return _soniq("migrations", "export", *args).stdout


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
