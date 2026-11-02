"""Planted overloads in public must never run from Soniq's own queries.

On Postgres 14 and older any role can create in public. An unqualified
built-in called with an untyped $n parameter, or an operator whose exact
types pg_catalog lacks, resolves to a planted overload that fits better.
The plant then runs as whoever runs the query: the superuser for setup,
the worker role for claims and maintenance.
"""

import uuid
from datetime import timedelta
from urllib.parse import urlparse, urlunparse

import asyncpg
import pytest

from soniq import Soniq
from soniq.core.worker import Worker
from soniq.features.dead_letter import DeadLetterFilter
from tests.db_utils import TEST_DATABASE_URL

# Each plant records current_user, then does what the built-in would do so
# the flow keeps going and a later plant still gets its chance.
_LOG = "INSERT INTO public.hijack_fired VALUES (current_user, '{0}');"
PLANTS = [
    (
        "pg_advisory_lock(k float8) RETURNS void",
        "PERFORM pg_catalog.pg_advisory_lock(k::bigint);",
    ),
    (
        "pg_advisory_unlock(k float8) RETURNS boolean",
        "RETURN pg_catalog.pg_advisory_unlock(k::bigint);",
    ),
    (
        "pg_try_advisory_lock(k float8) RETURNS boolean",
        "RETURN pg_catalog.pg_try_advisory_lock(k::bigint);",
    ),
    (
        "pg_advisory_xact_lock(k integer) RETURNS void",
        "PERFORM pg_catalog.pg_advisory_xact_lock(k::bigint);",
    ),
    (
        "pg_advisory_xact_lock(k float8) RETURNS void",
        "PERFORM pg_catalog.pg_advisory_xact_lock(k::bigint);",
    ),
    (
        "make_interval(hours float8) RETURNS interval",
        "RETURN pg_catalog.make_interval(hours => hours::int);",
    ),
    (
        "make_interval(secs numeric) RETURNS interval",
        "RETURN pg_catalog.make_interval(secs => secs::float8);",
    ),
    ("hashtext(t varchar) RETURNS integer", "RETURN pg_catalog.hashtext(t::text);"),
    (
        "pg_notify(c varchar, p varchar) RETURNS void",
        "PERFORM pg_catalog.pg_notify(c::text, p::text);",
    ),
    (
        "date_trunc(f varchar, t timestamptz) RETURNS timestamptz",
        "RETURN pg_catalog.date_trunc(f::text, t);",
    ),
    (
        "planted_mul(a numeric, b integer) RETURNS numeric",
        "RETURN a OPERATOR(pg_catalog.*) b::numeric;",
    ),
    (
        "planted_cat(a float8, b text) RETURNS text",
        "RETURN a::text OPERATOR(pg_catalog.||) b;",
    ),
    (
        "planted_arrow(j jsonb, k varchar) RETURNS text",
        "RETURN j OPERATOR(pg_catalog.->>) k::text;",
    ),
    (
        "planted_agg(s text[], v text) RETURNS text[]",
        "RETURN pg_catalog.array_append(s, v);",
    ),
]
OPERATORS = [
    "CREATE OPERATOR public.* (LEFTARG = numeric, RIGHTARG = integer,"
    " FUNCTION = public.planted_mul)",
    "CREATE OPERATOR public.|| (LEFTARG = float8, RIGHTARG = text,"
    " FUNCTION = public.planted_cat)",
    "CREATE OPERATOR public.->> (LEFTARG = jsonb, RIGHTARG = varchar,"
    " FUNCTION = public.planted_arrow)",
    "CREATE AGGREGATE public.array_agg(text)"
    " (SFUNC = public.planted_agg, STYPE = text[])",
]


def _db_url(url: str, name: str) -> str:
    return urlunparse(urlparse(url)._replace(path=f"/{name}"))


@pytest.fixture
async def hijack_db():
    """A fresh database where an attacker role has planted overloads."""
    base = urlparse(TEST_DATABASE_URL).path.lstrip("/")
    name = f"{base}_hijack"
    role = f"soniq_attacker_{uuid.uuid4().hex[:8]}"
    admin = await asyncpg.connect(TEST_DATABASE_URL)
    await admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
    await admin.execute(f'CREATE DATABASE "{name}"')
    await admin.execute(f"CREATE ROLE {role}")
    url = _db_url(TEST_DATABASE_URL, name)
    conn = await asyncpg.connect(url)
    try:
        # Postgres 15+ took CREATE on public away; give it back to stand in
        # for an older server.
        await conn.execute(f"GRANT CREATE, USAGE ON SCHEMA public TO {role}")
        await conn.execute(f"SET ROLE {role}")
        await conn.execute("CREATE TABLE public.hijack_fired (who text, what text)")
        await conn.execute("GRANT INSERT ON public.hijack_fired TO PUBLIC")
        for signature, body in PLANTS:
            await conn.execute(
                f"CREATE FUNCTION public.{signature} LANGUAGE plpgsql AS $$"
                f"BEGIN {_LOG.format(signature)} {body} END$$"
            )
        for sql in OPERATORS:
            await conn.execute(sql)
        await conn.execute("RESET ROLE")
        yield url, conn
    finally:
        await conn.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.execute(f"DROP ROLE IF EXISTS {role}")
        await admin.close()


@pytest.mark.asyncio
async def test_planted_overloads_never_run(hijack_db):
    url, conn = hijack_db
    app = Soniq(database_url=url)

    @app.job(name="hijack.echo")
    async def echo(n: int):
        return {"n": n}

    @app.job(name="hijack.fail", retries=0)
    async def fail():
        raise RuntimeError("boom")

    try:
        # Setup and migrate-status take the migration advisory lock.
        await app.setup()
        await app._get_migration_status()

        # Worker: claim, finish, fail, retry, snooze, dead-letter.
        await app.enqueue(echo, n=1)
        await app.enqueue(fail)
        await app.run_worker(run_once=True)
        backend = app.backend
        job_id = await app.enqueue(echo, n=2)
        await backend.mark_job_failed(job_id, attempts=1, error="x", retry_delay=1.5)
        await backend.reschedule_job(job_id, delay_seconds=2, attempts=1)
        await backend.mark_job_dead_letter(
            job_id, attempts=1, error="x", reason="test", tags={"k": "v"}
        )

        # Maintenance pass, worker registry and heartbeat.
        worker_id = str(uuid.uuid4())
        await backend.register_worker(
            worker_id=worker_id, hostname="h", pid=1, queues=[], concurrency=1
        )
        await backend.update_heartbeat(worker_id)
        await Worker(backend, app._job_registry)._maybe_cleanup()

        # Scheduler upsert and one due tick.
        await app.scheduler.add(echo, every=timedelta(minutes=5), args={"n": 3})
        await conn.execute(
            "UPDATE soniq_recurring_jobs SET next_run = NOW() - INTERVAL '1 minute'"
        )
        app.scheduler._loaded = False
        await app.scheduler._tick()

        # Dashboard queries.
        dash = app.dashboard_data
        await dash.get_job_stats()
        await dash.get_recent_jobs()
        await dash.get_queue_stats()
        await dash.get_job_metrics(hours=24)
        await dash.get_job_timeline(hours=24)
        await dash.get_job_types_stats()
        await dash.get_worker_stats()
        await dash.get_system_health()
        await dash.get_task_registry_drift()
        await dash.search_jobs(query="echo")
        await dash.get_job_details(str(job_id))
        await dash.get_dead_letter_jobs()
        await dash.get_workers()

        # Dead-letter and log queries. The log row makes array_agg run.
        await conn.execute(
            "INSERT INTO soniq_logs (timestamp, level, message, logger_name, job_name)"
            " VALUES (NOW(), 'ERROR', 'm', 'l', 'hijack.echo')"
        )
        await app.dead_letter.get_dead_letter_stats(hours=24)
        await app.dead_letter.list_dead_letter_jobs(
            DeadLetterFilter(tags={"k": "v"}, queues=["default"])
        )
        await app.dead_letter.cleanup_old_dead_letter_jobs(days=30, dry_run=True)
        await app.dead_letter.add_tags_to_job(str(job_id), {"a": "b"})
        await app.dead_letter.replay(str(job_id))
        await app.dead_letter.bulk_delete(DeadLetterFilter(tags={"a": "b"}))
        await app.logs.get_error_summary(hours=24)
        await app.logs.search_logs("x", hours=24)
    finally:
        await app.close()

    fired = await conn.fetch("SELECT who, what FROM public.hijack_fired")
    assert sorted({r["what"] for r in fired}) == []
