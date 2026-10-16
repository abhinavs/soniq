"""The public surface of soniq 0.0.5 must still exist, unchanged.

``surface_0_0_5.json`` is a snapshot of the 0.0.5 surface. This test
builds the same data from the code under test and fails when anything in
the snapshot is missing or different. New names, methods, parameters,
settings, CLI flags, tables and columns are allowed.

Two places allow no additions at all: the keywords that ``enqueue()`` and
``enqueue_many()`` reserve. ``enqueue(job, **kwargs)`` passes unknown
keywords to the job, so a new reserved keyword would take that name away
from existing job signatures.

To regenerate the snapshot (only when the surface grows):

    python -m tests.compat.test_surface postgresql://postgres@localhost/soniq_compat
"""

import argparse
import asyncio
import inspect
import json
import sys
from pathlib import Path

import asyncpg

import soniq
import soniq.testing
from soniq import Soniq
from soniq.cli.main import build_parser
from soniq.settings import SoniqSettings

SNAPSHOT = Path(__file__).with_name("surface_0_0_5.json")


def _jsonable(value):
    return json.loads(json.dumps(value, default=repr))


def _signature(func) -> list:
    params = []
    for p in inspect.signature(func).parameters.values():
        entry = {"name": p.name, "kind": p.kind.name}
        if p.default is not inspect.Parameter.empty:
            entry["default"] = _jsonable(p.default)
        params.append(entry)
    return params


def _soniq_methods() -> dict:
    out = {}
    for name, member in inspect.getmembers(Soniq):
        if name.startswith("_") and name not in ("__init__", "__aenter__", "__aexit__"):
            continue
        if isinstance(member, property):
            out[name] = {"type": "property", "settable": member.fset is not None}
        elif inspect.isfunction(member):
            out[name] = {
                "type": "async" if inspect.iscoroutinefunction(member) else "sync",
                "params": _signature(member),
            }
    return out


def _reserved_keywords(func) -> dict:
    params = inspect.signature(func).parameters.values()
    return {
        "keywords": sorted(
            p.name
            for p in params
            if p.kind in (p.KEYWORD_ONLY, p.POSITIONAL_OR_KEYWORD) and p.name != "self"
        ),
        "var_keyword": any(p.kind is p.VAR_KEYWORD for p in params),
    }


def _settings() -> dict:
    prefix = SoniqSettings.model_config.get("env_prefix", "")
    out = {}
    for name, field in SoniqSettings.model_fields.items():
        if field.default_factory is not None:
            default = field.default_factory()
        else:
            default = field.default
        alias = field.validation_alias or field.alias
        out[name] = {
            "default": _jsonable(default),
            "env": alias if isinstance(alias, str) else f"{prefix}{name}".upper(),
        }
    return out


def _parser_options(parser: argparse.ArgumentParser) -> dict:
    out = {}
    for action in parser._actions:
        if isinstance(action, (argparse._HelpAction, argparse._SubParsersAction)):
            continue
        key = action.option_strings[0] if action.option_strings else action.dest
        out[key] = _jsonable(
            {
                "aliases": sorted(action.option_strings),
                "dest": action.dest,
                "action": type(action).__name__,
                "default": action.default,
                "type": getattr(action.type, "__name__", action.type),
                "choices": list(action.choices) if action.choices else None,
                "nargs": action.nargs,
                "required": action.required,
            }
        )
    return out


def _cli() -> dict:
    parser = build_parser()
    commands = {"": _parser_options(parser)}
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, sub in action.choices.items():
                commands[name] = _parser_options(sub)
    return commands


async def _tables(database_url: str) -> dict:
    app = Soniq(database_url=database_url)
    try:
        await app.setup()
        conn = await asyncpg.connect(database_url)
        try:
            rows = await conn.fetch(
                "SELECT table_name, column_name, data_type FROM "
                "information_schema.columns WHERE table_schema = 'public' "
                "AND table_name LIKE 'soniq%' ORDER BY table_name, column_name"
            )
        finally:
            await conn.close()
    finally:
        await app.close()
    tables: dict = {}
    for r in rows:
        tables.setdefault(r["table_name"], {})[r["column_name"]] = r["data_type"]
    return tables


def build_static_surface() -> dict:
    return {
        "soniq.__all__": sorted(soniq.__all__),
        "soniq.testing.__all__": sorted(soniq.testing.__all__),
        "Soniq": _soniq_methods(),
        "enqueue": _reserved_keywords(Soniq.enqueue),
        "enqueue_many": _reserved_keywords(Soniq.enqueue_many),
        "settings": _settings(),
        "cli": _cli(),
    }


# --- comparison --------------------------------------------------------------


def _missing_or_changed(old, new, path="") -> list:
    """Every key in ``old`` must be in ``new`` with an equal value. Extra keys in
    ``new`` are fine. Lists of names are treated as sets."""
    problems = []
    if isinstance(old, dict) and isinstance(new, dict):
        for key, value in old.items():
            if key not in new:
                problems.append(f"{path}/{key}: missing")
            else:
                problems += _missing_or_changed(value, new[key], f"{path}/{key}")
    elif isinstance(old, list) and isinstance(new, list) and path.endswith("__all__"):
        problems += [f"{path}: missing {n!r}" for n in old if n not in new]
    elif old != new:
        problems.append(f"{path}: was {old!r}, now {new!r}")
    return problems


def _signature_problems(path: str, old: list, new: list) -> list:
    """Old parameters must keep their name, kind and default. Old positional
    parameters must keep their order. New parameters need a default, unless
    they are ``*args`` / ``**kwargs``."""
    problems = []
    new_by_name = {p["name"]: p for p in new}
    for p in old:
        if p["name"] not in new_by_name:
            problems.append(f"{path}: parameter {p['name']!r} missing")
        elif new_by_name[p["name"]] != p:
            problems.append(f"{path}: parameter was {p}, now {new_by_name[p['name']]}")
    positional = ("POSITIONAL_ONLY", "POSITIONAL_OR_KEYWORD")
    old_pos = [p["name"] for p in old if p["kind"] in positional]
    new_pos = [p["name"] for p in new if p["kind"] in positional]
    if new_pos[: len(old_pos)] != old_pos:
        problems.append(f"{path}: positional order was {old_pos}, now {new_pos}")
    old_names = {p["name"] for p in old}
    for p in new:
        if (
            p["name"] not in old_names
            and "default" not in p
            and p["kind"] not in ("VAR_POSITIONAL", "VAR_KEYWORD")
        ):
            problems.append(f"{path}: new parameter {p['name']!r} has no default")
    return problems


def compare(old: dict, new: dict) -> list:
    problems = []
    for name, meta in old["Soniq"].items():
        cur = new["Soniq"].get(name)
        if cur is None:
            problems.append(f"Soniq.{name}: missing")
            continue
        if cur["type"] != meta["type"]:
            problems.append(f"Soniq.{name}: was {meta['type']}, now {cur['type']}")
        if meta["type"] == "property" and meta["settable"] and not cur.get("settable"):
            problems.append(f"Soniq.{name}: no longer settable")
        if "params" in meta and "params" in cur:
            problems += _signature_problems(
                f"Soniq.{name}", meta["params"], cur["params"]
            )
    for fn in ("enqueue", "enqueue_many"):
        if old[fn] != new[fn]:
            problems.append(
                f"{fn}() reserved keywords changed: was {old[fn]}, now {new[fn]}"
            )
    rest = {
        k: v
        for k, v in old.items()
        if k not in ("Soniq", "enqueue", "enqueue_many", "tables")
    }
    problems += _missing_or_changed(rest, new)
    return problems


# --- tests -------------------------------------------------------------------


def _snapshot() -> dict:
    return json.loads(SNAPSHOT.read_text())


def test_public_names_still_import():
    for name in _snapshot()["soniq.__all__"]:
        assert hasattr(soniq, name), name
    for name in _snapshot()["soniq.testing.__all__"]:
        assert hasattr(soniq.testing, name), name


def test_static_surface_matches_snapshot():
    old = _snapshot()
    problems = compare(old, build_static_surface())
    assert not problems, "Public surface changed:\n  " + "\n  ".join(problems)


def test_settings_env_vars_are_read(monkeypatch):
    """Every 0.0.5 setting still reads its env var."""
    snap = _snapshot()["settings"]
    monkeypatch.setenv(snap["environment"]["env"], "compat-check")
    monkeypatch.setenv(snap["concurrency"]["env"], "7")
    monkeypatch.setenv(snap["queues"]["env"], "a,b")
    s = SoniqSettings()
    assert (s.environment, s.concurrency, s.queues) == ("compat-check", 7, ["a", "b"])


async def test_schema_matches_snapshot(fresh_db):
    url = fresh_db("soniq_compat_schema")
    problems = _missing_or_changed(_snapshot()["tables"], await _tables(url), "tables")
    assert not problems, "Schema changed:\n  " + "\n  ".join(problems)


if __name__ == "__main__":
    surface = build_static_surface()
    surface["tables"] = asyncio.run(_tables(sys.argv[1]))
    SNAPSHOT.write_text(json.dumps(surface, indent=2, sort_keys=True) + "\n")
    print(f"wrote {SNAPSHOT}")
