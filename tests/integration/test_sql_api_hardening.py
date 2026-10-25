"""Migration 0006 hardening: planted functions can't hijack soniq_enqueue(),
SQL and Python hash the same args the same way, and bad input gets a hint.
"""

import json
import uuid
from pathlib import Path

import asyncpg
import pytest

from soniq import Soniq, SoniqError
from soniq.utils.hashing import compute_args_hash
from tests.db_utils import TEST_DATABASE_URL

MIGRATION = (
    Path(__file__).parents[2] / "soniq/backends/postgres/migrations/0006_sql_api.sql"
)

# Before the fix each of these beat the built-in on argument types and ran
# with the function owner's rights.
PLANTS = [
    "CREATE FUNCTION public.convert_to(text, text) RETURNS bytea"
    " LANGUAGE plpgsql AS $$BEGIN RAISE 'planted convert_to ran'; END$$",
    "CREATE FUNCTION public.to_json(text) RETURNS json"
    " LANGUAGE plpgsql AS $$BEGIN RAISE 'planted to_json ran'; END$$",
    "CREATE FUNCTION public.planted_path(jsonb, jsonb) RETURNS text"
    " LANGUAGE plpgsql AS $$BEGIN RAISE 'planted #>> ran'; END$$",
    "CREATE OPERATOR public.#>> (LEFTARG = jsonb, RIGHTARG = jsonb,"
    " FUNCTION = public.planted_path)",
    "CREATE FUNCTION public.planted_check(text) RETURNS boolean"
    " LANGUAGE plpgsql AS $$BEGIN RAISE 'planted domain ran'; END$$",
    "CREATE DOMAIN pg_temp.text AS pg_catalog.text CHECK (public.planted_check(VALUE))",
]


@pytest.fixture
async def app():
    a = Soniq(database_url=TEST_DATABASE_URL)

    @a.job(name="sqlapi.send", unique=True)
    async def send(n: int):
        return {"n": n}

    await a.setup()
    yield a
    await a._backend.reset()
    await a.close()


@pytest.mark.asyncio
async def test_planted_functions_never_run_as_the_owner(app):
    role = f"soniq_attacker_{uuid.uuid4().hex[:8]}"
    async with app.backend.acquire() as conn:
        schema = await conn.fetchval("SELECT current_schema()")
        await conn.execute(f"CREATE ROLE {role}")
        try:
            # Postgres 15+ took CREATE on public away from everyone; give it back
            # to stand in for an older server.
            await conn.execute(f"GRANT CREATE, USAGE ON SCHEMA {schema} TO {role}")
            await conn.execute(f"GRANT EXECUTE ON FUNCTION soniq_enqueue TO {role}")
            await conn.execute(f"SET ROLE {role}")
            for sql in PLANTS:
                await conn.execute(sql.replace("public.", f"{schema}."))
            job_id = await conn.fetchval(
                "SELECT soniq_enqueue('sqlapi.send', '{\"n\": 1}', unique_job => true)"
            )
            await conn.execute("RESET ROLE")
            row = await conn.fetchrow(
                "SELECT args_hash FROM soniq_jobs WHERE id = $1", job_id
            )
        finally:
            await conn.execute("RESET ROLE")
            await conn.execute("DROP DOMAIN IF EXISTS pg_temp.text")
            await conn.execute(f"DROP OWNED BY {role}")
            await conn.execute(f"DROP ROLE {role}")
    assert row["args_hash"] == compute_args_hash({"n": 1})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "args",
    [
        {"f": 1e16},
        {"f": 1e-07},
        {"f": 1e100},
        {"f": 1.5e300},
        {"f": -0.0},
        {"f": 0.1, "g": 1.0, "h": -2.5e-5},
        {10: "x", 2: "y"},
        {1: "a", "b": 2},
    ],
)
async def test_sql_hash_matches_python_for_floats_and_keys(app, args):
    async with app.backend.acquire() as conn:
        sql = await conn.fetchval(
            "SELECT soniq_args_hash($1::text::jsonb)", json.dumps(args)
        )
    assert sql == compute_args_hash(args)


def test_int_keys_hash_like_string_keys():
    assert compute_args_hash({10: "x"}) == compute_args_hash({"10": "x"})


def _nest(levels):
    v = 1
    for _ in range(levels):
        v = [v]
    return {"a": v}


@pytest.mark.asyncio
async def test_unique_args_depth_limit_is_the_same_in_sql_and_python(app):
    ok, too_deep = _nest(498), _nest(499)
    async with app.backend.acquire() as conn:
        job_id = await conn.fetchval(
            "SELECT soniq_enqueue('sqlapi.send', $1::text::jsonb, unique_job => true)",
            json.dumps(ok),
        )
        stored = await conn.fetchval(
            "SELECT args_hash FROM soniq_jobs WHERE id = $1", job_id
        )
        with pytest.raises(asyncpg.InvalidParameterValueError) as exc:
            await conn.fetchval(
                "SELECT soniq_enqueue('sqlapi.send', $1::text::jsonb,"
                " unique_job => true)",
                json.dumps(too_deep),
            )
    assert stored == compute_args_hash(ok)
    assert exc.value.hint == "SONIQ_TASK_ARGS_INVALID"
    with pytest.raises(SoniqError) as err:
        compute_args_hash(too_deep)
    assert err.value.error_code == "SONIQ_TASK_ARGS_INVALID"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "call, hint",
    [
        ("soniq_enqueue('sqlapi.send', '{\"n\": 1e400}')", "SONIQ_TASK_ARGS_INVALID"),
        (
            "soniq_enqueue('sqlapi.send', '{\"n\": [-1e309]}')",
            "SONIQ_TASK_ARGS_INVALID",
        ),
        (
            "soniq_enqueue('sqlapi.send', max_attempts => 0)",
            "SONIQ_INVALID_MAX_ATTEMPTS",
        ),
        (
            "soniq_enqueue('sqlapi.send', max_attempts => -1)",
            "SONIQ_INVALID_MAX_ATTEMPTS",
        ),
    ],
)
async def test_sql_enqueue_rejects_bad_input_with_a_hint(app, call, hint):
    async with app.backend.acquire() as conn:
        with pytest.raises(asyncpg.InvalidParameterValueError) as exc:
            await conn.fetchval(f"SELECT {call}")
    assert exc.value.hint == hint


@pytest.mark.asyncio
async def test_sql_enqueue_stores_null_max_attempts(app):
    async with app.backend.acquire() as conn:
        job_id = await conn.fetchval(
            "SELECT soniq_enqueue('sqlapi.send', '{\"n\": 1}', max_attempts => NULL)"
        )
        got = await conn.fetchval(
            "SELECT max_attempts IS NULL FROM soniq_jobs WHERE id = $1", job_id
        )
    assert got is True


@pytest.mark.asyncio
async def test_migration_can_run_twice(app):
    sql = MIGRATION.read_text(encoding="utf-8")
    async with app.backend.acquire() as conn:
        async with conn.transaction():
            await conn.execute(sql)
        path = await conn.fetchval(
            "SELECT array_to_string(proconfig, ',') FROM pg_proc"
            " WHERE proname = 'soniq_enqueue'"
        )
        job_id = await conn.fetchval("SELECT soniq_enqueue('sqlapi.send')")
    assert path.startswith("search_path=pg_catalog, ")
    assert path.endswith(", pg_temp")
    assert job_id is not None
