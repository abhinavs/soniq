"""
Dashboard regressions: one API key for middleware, write guard and page;
delete/health/search edge cases; dead-letter tags; timeline and queue totals.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest

from soniq import Soniq
from soniq.dashboard.server import FASTAPI_AVAILABLE, create_dashboard_app
from tests.db_utils import TEST_DATABASE_URL

pytestmark = pytest.mark.skipif(
    not FASTAPI_AVAILABLE
    or (not os.environ.get("SONIQ_DATABASE_URL") and not TEST_DATABASE_URL),
    reason="FastAPI or Postgres test DB not available",
)

httpx = pytest.importorskip("httpx")


@pytest.fixture
async def app():
    a = Soniq(database_url=TEST_DATABASE_URL)
    await a.ensure_initialized()
    pool = await a._get_pool()
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE soniq_jobs, soniq_dead_letter_jobs CASCADE")
    yield a
    await a.close()


def _client(dash_app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=dash_app), base_url="http://x"
    )


async def _insert_job(app, status="queued", job_name="t.job", queue="default"):
    job_id = uuid.uuid4()
    pool = await app._get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO soniq_jobs (id, job_name, args, status, queue, max_attempts)"
            " VALUES ($1, $2, '{}'::jsonb, $3, $4, 3)",
            job_id,
            job_name,
            status,
            queue,
        )
    return str(job_id)


async def _insert_dlq(app, *, queue="default", resurrection_count=0, tags=None):
    pool = await app._get_pool()
    now = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO soniq_dead_letter_jobs
                (id, job_name, args, queue, priority, max_attempts, attempts,
                 last_error, dead_letter_reason, original_created_at,
                 moved_to_dead_letter_at, resurrection_count, tags)
            VALUES ($1, 't.dlq', '{}'::jsonb, $2, 100, 3, 3, 'boom', 'max_retries',
                    $3, $3, $4, $5::jsonb)
            """,
            uuid.uuid4(),
            queue,
            now,
            resurrection_count,
            tags,
        )


async def test_key_set_after_build_is_not_trusted(app, monkeypatch):
    """A key that reaches the env after the app is built must not unlock
    the write guard (the middleware was never installed)."""
    monkeypatch.delenv("SONIQ_DASHBOARD_API_KEY", raising=False)
    monkeypatch.setenv("SONIQ_DASHBOARD_WRITE_ENABLED", "true")
    dash = create_dashboard_app(app)
    monkeypatch.setenv("SONIQ_DASHBOARD_API_KEY", "late")
    async with _client(dash) as c:
        r = await c.post(f"/api/jobs/{uuid.uuid4()}/cancel")
        assert r.status_code == 403
        assert "const CAN_WRITE = false" in (await c.get("/")).text


async def test_key_set_at_build_is_enforced_and_enables_page_writes(app, monkeypatch):
    monkeypatch.setenv("SONIQ_DASHBOARD_API_KEY", "k")
    monkeypatch.setenv("SONIQ_DASHBOARD_WRITE_ENABLED", "true")
    dash = create_dashboard_app(app)
    async with _client(dash) as c:
        assert (await c.get("/api/stats")).status_code == 401
        assert (
            await c.get("/api/stats", headers={"X-API-Key": "k"})
        ).status_code == 200
        assert "const CAN_WRITE = true" in (await c.get("/")).text


async def test_delete_refuses_running_job_and_404s_missing(app, monkeypatch):
    monkeypatch.setenv("SONIQ_DASHBOARD_API_KEY", "k")
    monkeypatch.setenv("SONIQ_DASHBOARD_WRITE_ENABLED", "true")
    h = {"X-API-Key": "k"}
    running = await _insert_job(app, status="processing")
    queued = await _insert_job(app, status="queued")
    async with _client(create_dashboard_app(app)) as c:
        assert (await c.delete(f"/api/jobs/{running}", headers=h)).status_code == 409
        assert (await c.get(f"/api/jobs/{running}", headers=h)).status_code == 200
        assert (await c.delete(f"/api/jobs/{queued}", headers=h)).status_code == 200
        assert (
            await c.delete(f"/api/jobs/{uuid.uuid4()}", headers=h)
        ).status_code == 404
        r = await c.post(f"/api/dead-letter/{uuid.uuid4()}/replay", headers=h)
        assert r.status_code == 404


async def test_health_reports_unhealthy_when_db_down():
    dash = create_dashboard_app(
        Soniq(database_url="postgresql://postgres@localhost:1/nope")
    )
    async with _client(dash) as c:
        r = await c.get("/api/system/health")
    assert r.status_code == 200
    assert r.json()["database_healthy"] is False


async def test_search_filters(app):
    await _insert_job(app, job_name="a.b_c")
    await _insert_job(app, job_name="a.bxc")
    async with _client(create_dashboard_app(app)) as c:
        r = await c.get("/api/jobs/search", params={"query": "b_c"})
        assert [j["job_name"] for j in r.json()["jobs"]] == ["a.b_c"]
        r = await c.get("/api/jobs/search", params={"query": "%"})
        assert r.json()["total_count"] == 0
        for field in ("query", "queue", "status", "job_name"):
            r = await c.get("/api/jobs/search", params={field: "a\x00b"})
            assert r.status_code == 422
        assert (await c.get("/api/jobs", params={"queue": "a\x00b"})).status_code == 422


async def test_timeline_skips_replayed_dead_letters(app):
    await _insert_dlq(app, resurrection_count=1)
    await _insert_dlq(app, resurrection_count=0)
    async with _client(create_dashboard_app(app)) as c:
        rows = (await c.get("/api/jobs/timeline")).json()
    assert sum(r["dead_letter"] for r in rows) == 1


async def test_worker_stats_active_jobs_counts_running_only(app):
    await _insert_job(app, status="queued")
    await _insert_job(app, status="processing")
    async with _client(create_dashboard_app(app)) as c:
        assert (await c.get("/api/workers/stats")).json()["active_jobs"] == 1


async def test_queues_dlq_only_queue_sorted_last_with_zero_live_jobs(app):
    await _insert_job(app, queue="live")
    await _insert_dlq(app, queue="dead")
    await _insert_dlq(app, queue="dead")
    async with _client(create_dashboard_app(app)) as c:
        rows = (await c.get("/api/queues")).json()
    assert [r["queue"] for r in rows] == ["live", "dead"]
    assert rows[1]["total_jobs"] == 0 and rows[1]["dead_letter"] == 2


# Legacy 0.1.0 rows hold the tags as a JSON string inside the JSONB.
async def test_dead_letter_tags_are_normalised(app):
    await _insert_dlq(app, tags='{"team": "x"}')
    async with _client(create_dashboard_app(app)) as c:
        jobs = (await c.get("/api/dead-letter")).json()["jobs"]
    assert jobs[0]["tags"] == {"team": "x"}
