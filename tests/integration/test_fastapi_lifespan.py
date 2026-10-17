"""FastAPI lifespan helper: in-process worker, graceful stop, opt-in only."""

import asyncio
import os
import threading
import time
from contextlib import asynccontextmanager

import pytest

pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from soniq import Soniq  # noqa: E402
from soniq.integrations.fastapi import lifespan  # noqa: E402
from tests.db_utils import TEST_DATABASE_URL  # noqa: E402


def _wait(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def _status(job_id):
    async def get():
        probe = Soniq(database_url=TEST_DATABASE_URL)
        try:
            job = await probe.get_job(job_id)
            return job["status"] if job else None
        finally:
            await probe.close()

    return asyncio.run(get())


def test_job_enqueued_in_route_runs_in_process():
    soniq = Soniq(database_url=TEST_DATABASE_URL)
    ran = threading.Event()

    @soniq.job()
    async def ping():
        ran.set()

    app = FastAPI(lifespan=lifespan(soniq, run_worker=True, concurrency=1))

    @app.post("/ping")
    async def route():
        return {"id": await soniq.enqueue(ping)}

    with TestClient(app) as client:
        assert client.post("/ping").status_code == 200
        assert _wait(ran.is_set)
    assert soniq.is_closed


def test_in_flight_job_finishes_on_shutdown():
    soniq = Soniq(database_url=TEST_DATABASE_URL)
    started, finished = threading.Event(), threading.Event()

    @soniq.job()
    async def slow():
        started.set()
        await asyncio.sleep(1.0)
        finished.set()

    app = FastAPI(lifespan=lifespan(soniq, run_worker=True, concurrency=1))

    @app.post("/slow")
    async def route():
        return {"id": await soniq.enqueue(slow)}

    with TestClient(app) as client:
        job_id = client.post("/slow").json()["id"]
        assert started.wait(10)
    assert finished.is_set()
    assert _status(job_id) == "done"


def test_run_worker_false_starts_no_worker():
    soniq = Soniq(database_url=TEST_DATABASE_URL)
    ran = threading.Event()

    @soniq.job()
    async def ping():
        ran.set()

    app = FastAPI(lifespan=lifespan(soniq))

    @app.post("/ping")
    async def route():
        return {"id": await soniq.enqueue(ping)}

    with TestClient(app) as client:
        job_id = client.post("/ping").json()["id"]
        time.sleep(1.0)
        assert not ran.is_set()
    assert _status(job_id) == "queued"


def test_extra_lifespan_runs_inside():
    soniq = Soniq(database_url=TEST_DATABASE_URL)
    events = []

    @asynccontextmanager
    async def mine(app):
        events.append(("start", soniq.is_initialized))
        yield
        events.append(("stop", soniq.is_closed))

    with TestClient(FastAPI(lifespan=lifespan(soniq, extra=mine))):
        pass
    assert events == [("start", True), ("stop", False)]


def test_in_process_worker_logs_pid_and_concurrency(caplog):
    soniq = Soniq(database_url=TEST_DATABASE_URL)
    app = FastAPI(lifespan=lifespan(soniq, run_worker=True, concurrency=3))

    with caplog.at_level("INFO", logger="soniq.integrations.fastapi"):
        with TestClient(app):
            pass
    messages = [r.getMessage() for r in caplog.records]
    assert any(f"pid {os.getpid()}" in m and "concurrency 3" in m for m in messages)
