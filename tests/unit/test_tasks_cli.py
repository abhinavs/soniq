"""
Tests for ``soniq tasks-check``.

The historical ``tasks-list`` subcommand was removed in 0.0.2 along
with the process-global registry: with per-instance registries there
is no single "current process registry" to dump, and the dashboard
already exposes the fleet-wide ``soniq_task_registry`` table for the
same purpose. The remaining ``tasks-check`` codemod compares stub-
package TaskRefs to the shared registry table so CI can block deploys
on drift.
"""

from __future__ import annotations

import io
import os
import sys
from contextlib import redirect_stderr
from types import SimpleNamespace

import pytest

from tests.db_utils import TEST_DATABASE_URL

os.environ.setdefault("SONIQ_DATABASE_URL", TEST_DATABASE_URL)

from soniq.cli.main import build_parser  # noqa: E402
from soniq.cli.tasks import (  # noqa: E402
    _load_task_refs_from_package,
    handle_tasks_check,
)


def test_tasks_check_help_text_mentions_shared_registry():
    parser = build_parser()
    help_text = _subcommand_help(parser, "tasks-check")
    assert (
        "shared registry" in help_text.lower() or "registry table" in help_text.lower()
    )


def test_tasks_check_without_database_url_emits_helpful_error(monkeypatch):
    """``check`` needs a live DB connection. If SONIQ_DATABASE_URL is
    missing AND ``--database-url`` is unset, the codemod must fail with
    a clear hint pointing at the env var name (so an operator running
    it in a CI container without the env var gets a readable hint
    instead of a connection traceback)."""
    monkeypatch.delenv("SONIQ_DATABASE_URL", raising=False)
    err = io.StringIO()
    with redirect_stderr(err):
        rc = handle_tasks_check(SimpleNamespace(package="some_pkg", database_url=None))
    assert rc == 2
    assert "SONIQ_DATABASE_URL" in err.getvalue()


def test_tasks_check_without_package_arg_errors(monkeypatch):
    monkeypatch.setenv("SONIQ_DATABASE_URL", "postgresql://nowhere/db")
    err = io.StringIO()
    with redirect_stderr(err):
        rc = handle_tasks_check(SimpleNamespace(package=None, database_url=None))
    assert rc == 2
    msg = err.getvalue().lower()
    assert "package" in msg or "stub" in msg


def test_tasks_check_imports_dotted_module_from_cwd(tmp_path, monkeypatch):
    """``tasks-check stubs_mod`` must find a module in the current
    folder, like ``worker`` and ``setup`` do."""
    (tmp_path / "cwd_stubs_mod.py").write_text(
        "from soniq import task_ref\n" "send = task_ref(name='billing.send_invoice')\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "path", [p for p in sys.path if p not in ("", ".")])
    monkeypatch.delitem(sys.modules, "cwd_stubs_mod", raising=False)
    refs = _load_task_refs_from_package("cwd_stubs_mod")
    assert [r["name"] for r in refs] == ["billing.send_invoice"]


def test_version_flag_prints_package_version(capsys):
    from soniq import __version__

    with pytest.raises(SystemExit):
        build_parser().parse_args(["--version"])
    assert capsys.readouterr().out.strip() == f"soniq {__version__}"


def _subcommand_help(parser, name: str) -> str:
    """Render the ``--help`` text for a named subcommand.

    Walks the top-level parser to find the subparser for ``name`` and
    formats its help into a string. Used to pin help text wording so a
    refactor cannot silently drop the disambiguation that operators
    rely on.
    """
    for action in parser._actions:
        if isinstance(action, getattr(__import__("argparse"), "_SubParsersAction")):
            sub = action.choices.get(name)
            assert sub is not None, f"subcommand {name!r} not registered"
            return sub.format_help()
    raise AssertionError("no subparsers on parser")
