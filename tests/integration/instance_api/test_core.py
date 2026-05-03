"""
Test suite for Soniq core functionality
"""

import logging
import os
import uuid

import pytest
from pydantic import BaseModel

from soniq import Soniq
from tests.db_utils import TEST_DATABASE_URL

logging.basicConfig(level=logging.INFO)

os.environ["SONIQ_DATABASE_URL"] = TEST_DATABASE_URL


class SampleJobArgs(BaseModel):
    x: int
    y: int


_flaky_job_fail_counts = {}


@pytest.mark.asyncio
async def test_enqueue_and_run_job():
    """Test job enqueue and processing with instance-based architecture"""
    app = Soniq(database_url=TEST_DATABASE_URL)

    @app.job(name="instance_sample_job", retries=5, args_model=SampleJobArgs)
    async def instance_sample_job(x, y):
        return x + y

    instance_pool = await app._get_pool()
    async with instance_pool.acquire() as conn:
        await conn.execute("DELETE FROM soniq_jobs")

    job_id = await app.enqueue("instance_sample_job", args={"x": 1, "y": 2})
    assert job_id

    await app.run_worker(run_once=True)

    async with instance_pool.acquire() as conn:
        job_record = await conn.fetchrow(
            "SELECT * FROM soniq_jobs WHERE id = $1", uuid.UUID(job_id)
        )

        assert job_record is not None, f"Job {job_id} not found in database"
        assert job_record["job_name"] == "instance_sample_job"
        assert job_record["args"] == {"x": 1, "y": 2}
        assert job_record["max_attempts"] == 6  # retries=5 -> max_attempts=6
        assert job_record["status"] == "done"

    await app.close()


@pytest.mark.asyncio
async def test_enqueue_job_invalid_args():
    app = Soniq(database_url=TEST_DATABASE_URL)

    @app.job(name="sample_job", retries=5, args_model=SampleJobArgs)
    async def sample_job(x, y):
        return x + y

    pool = await app._get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM soniq_jobs")

    from soniq.errors import SONIQ_TASK_ARGS_INVALID, SoniqError

    with pytest.raises(SoniqError) as exc_info:
        await app.enqueue("sample_job", args={"x": 1, "y": "invalid"})
    assert exc_info.value.error_code == SONIQ_TASK_ARGS_INVALID

    await app.close()


@pytest.mark.asyncio
async def test_retry_mechanism():
    global _flaky_job_fail_counts
    _flaky_job_fail_counts = {}

    app = Soniq(database_url=TEST_DATABASE_URL)

    @app.job(name="flaky_job", retries=3)
    async def flaky_job(job_id: str, should_fail: bool):
        if should_fail:
            _flaky_job_fail_counts[job_id] = _flaky_job_fail_counts.get(job_id, 0) + 1
            if _flaky_job_fail_counts[job_id] <= 2:
                raise ValueError("Simulated failure")
        return "Success"

    @app.job(name="always_fail_job", retries=3)
    async def always_fail_job(job_id: str):
        raise ValueError("Always fails")

    pool = await app._get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM soniq_jobs")

    job_id_1 = str(uuid.uuid4())
    actual_job_id_1 = await app.enqueue(
        "flaky_job", args={"job_id": job_id_1, "should_fail": True}
    )

    await app.run_worker(run_once=True)

    async with pool.acquire() as conn:
        job_record = await conn.fetchrow(
            "SELECT * FROM soniq_jobs WHERE id = $1", uuid.UUID(actual_job_id_1)
        )
        assert job_record["status"] == "done"
        assert job_record["attempts"] == 3  # 3 fetches (2 failures + 1 success)

    job_id_2 = str(uuid.uuid4())
    actual_job_id_2 = await app.enqueue("always_fail_job", args={"job_id": job_id_2})

    await app.run_worker(run_once=True)

    async with pool.acquire() as conn:
        in_jobs = await conn.fetchrow(
            "SELECT * FROM soniq_jobs WHERE id = $1", uuid.UUID(actual_job_id_2)
        )
        assert in_jobs is None
        dlq_record = await conn.fetchrow(
            "SELECT * FROM soniq_dead_letter_jobs WHERE id = $1",
            uuid.UUID(actual_job_id_2),
        )
        assert dlq_record is not None
        assert dlq_record["attempts"] == 4  # retries=3 means max_attempts=4
        assert "Always fails" in dlq_record["last_error"]

    await app.close()


@pytest.mark.asyncio
async def test_run_worker_processes_job():
    app = Soniq(database_url=TEST_DATABASE_URL)

    @app.job(name="sample_job", retries=5, args_model=SampleJobArgs)
    async def sample_job(x, y):
        return x + y

    pool = await app._get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM soniq_jobs")

    job_id = await app.enqueue("sample_job", args={"x": 10, "y": 20})

    await app.run_worker(run_once=True)

    async with pool.acquire() as conn:
        job_record = await conn.fetchrow(
            "SELECT * FROM soniq_jobs WHERE id = $1", uuid.UUID(job_id)
        )
        assert job_record["status"] == "done"

    await app.close()


@pytest.mark.asyncio
async def test_cli_worker():
    from soniq.cli.main import main

    assert main is not None

    app = Soniq(database_url=TEST_DATABASE_URL)
    assert app.run_worker is not None
    await app.close()


@pytest.mark.asyncio
async def test_task_discovery():
    """Importing a module listed in SONIQ_JOBS_MODULES has the side effect
    of registering its jobs on the module's own Soniq instance. The same
    instance is then used to enqueue and run the job."""
    original_env = os.environ.copy()
    os.environ["SONIQ_JOBS_MODULES"] = "tests.fixtures.discovery_jobs.my_tasks"
    os.environ["SONIQ_DATABASE_URL"] = TEST_DATABASE_URL

    try:
        import importlib

        my_tasks = importlib.import_module("tests.fixtures.discovery_jobs.my_tasks")
        importlib.reload(my_tasks)
        app = my_tasks.app

        registry = app._get_job_registry()
        assert registry.get_job("discovered_job") is not None

        pool = await app._get_pool()
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM soniq_jobs")

        await app.enqueue(
            "discovered_job", args={"message": "Hello from discovered job!"}
        )

        await app.run_worker(run_once=True)

        async with pool.acquire() as conn:
            job_record = await conn.fetchrow(
                "SELECT * FROM soniq_jobs WHERE job_name = 'discovered_job'"
            )
            assert job_record["status"] == "done"

        await app.close()
    finally:
        os.environ.clear()
        os.environ.update(original_env)
