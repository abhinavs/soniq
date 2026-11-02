"""Regression tests for the dead-letter and setup CLI fixes."""

from __future__ import annotations

import argparse
import contextlib
import os

import pytest

from soniq.cli import dead_letter as cli_dl
from soniq.cli._context import cli_app
from soniq.features.dead_letter import DeadLetterService


def _args(**kw):
    base = dict(
        action="list", job_ids=[], app=None, all=False, filter=None, limit=50,
        days=30, dry_run=False, yes=True, format="csv", output=None,
        database_url=None, jobs_modules=None,
    )  # fmt: skip
    base.update(kw)
    return argparse.Namespace(**base)


class _FakeDL:
    async def list_dead_letter_jobs(self, _f):
        return []

    async def export_dead_letter_jobs(self, _f, format="json", output=None):
        return output


def _patch_app(monkeypatch, seen=None):
    @contextlib.asynccontextmanager
    async def fake(args):
        if seen is not None:
            seen.append(args.app)
        yield argparse.Namespace(dead_letter=_FakeDL())

    monkeypatch.setattr(cli_dl, "cli_app", fake)


@pytest.mark.asyncio
async def test_positional_app_is_used_for_list(monkeypatch):
    seen: list = []
    _patch_app(monkeypatch, seen)
    assert await cli_dl.handle_dead_letter(_args(job_ids=["myapp.jobs:soniq"])) == 0
    assert seen == ["myapp.jobs:soniq"]


@pytest.mark.asyncio
async def test_list_rejects_stray_positional(monkeypatch, capsys):
    _patch_app(monkeypatch)
    assert await cli_dl.handle_dead_letter(_args(job_ids=["nope_not_a_module"])) == 1
    assert "unexpected argument" in capsys.readouterr().err


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["replay", "delete"])
async def test_bad_uuid_names_the_id(monkeypatch, capsys, action):
    _patch_app(monkeypatch)
    rc = await cli_dl.handle_dead_letter(_args(action=action, job_ids=["abc"]))
    assert rc == 1
    assert "Invalid job id (not a UUID): abc" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_info_line_goes_to_stderr(capsys):
    async with cli_app(
        argparse.Namespace(database_url="postgresql://u@h/db", app=None)
    ):
        pass
    out = capsys.readouterr()
    assert "instance-based configuration" not in out.out
    assert "instance-based configuration" in out.err


@pytest.mark.asyncio
async def test_empty_csv_export_writes_header_to_output(tmp_path):
    svc = DeadLetterService.__new__(DeadLetterService)

    async def empty(_f=None):
        return []

    svc.list_dead_letter_jobs = empty
    out = tmp_path / "dlq.csv"
    assert await svc.export_dead_letter_jobs(format="csv", output=str(out)) == str(out)
    assert out.read_text().startswith("id,")
    assert not [p for p in os.listdir(".") if p.startswith("dead_letter_export_")]


@pytest.mark.asyncio
async def test_export_missing_output_dir_is_a_clean_error(tmp_path, capsys):
    out = tmp_path / "missing" / "dlq.csv"

    @contextlib.asynccontextmanager
    async def fake(args):
        svc = DeadLetterService.__new__(DeadLetterService)

        async def empty(_f=None):
            return []

        svc.list_dead_letter_jobs = empty
        yield argparse.Namespace(dead_letter=svc)

    cli_dl.cli_app = fake
    try:
        rc = await cli_dl.handle_dead_letter(_args(action="export", output=str(out)))
    finally:
        cli_dl.cli_app = cli_app
    assert rc == 1
    assert "Can't write" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_export_file_is_private_and_not_symlink_followed(tmp_path):
    svc = DeadLetterService.__new__(DeadLetterService)

    async def no_jobs(_f=None):
        return []

    svc.list_dead_letter_jobs = no_jobs
    out = tmp_path / "a.json"
    await svc.export_dead_letter_jobs(format="json", output=str(out))
    assert oct(out.stat().st_mode & 0o777) == "0o600"

    out.write_text("old")  # an existing regular file is still overwritten
    await svc.export_dead_letter_jobs(format="json", output=str(out))
    assert out.read_text().strip() == "[]"

    victim = tmp_path / "victim"
    victim.write_text("keep")
    link = tmp_path / "link.json"
    link.symlink_to(victim)
    with pytest.raises(OSError):
        await svc.export_dead_letter_jobs(format="csv", output=str(link))
    assert victim.read_text() == "keep"


@pytest.mark.asyncio
async def test_webhook_redirect_is_a_failed_delivery():
    from aiohttp import web
    from aiohttp.test_utils import TestServer

    from soniq.features.webhooks import HTTPTransport

    hits = []

    async def redirect(_req):
        raise web.HTTPTemporaryRedirect("/elsewhere")

    async def elsewhere(req):
        hits.append(req)
        return web.Response(text="reached")

    app = web.Application()
    app.router.add_post("/", redirect)
    app.router.add_post("/elsewhere", elsewhere)
    async with TestServer(app) as server:
        result = await HTTPTransport().deliver(
            url=str(server.make_url("/")), payload=b"{}", headers={}
        )
    assert not result.ok
    assert "redirect" in result.error
    assert hits == []
