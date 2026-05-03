"""
Tests for Soniq app operations not covered elsewhere.

Covers: close() error path, unregistered job error, transactional enqueue,
schedule(), _check_pool_sizing, hooks, get_pool, app as context manager.
"""

import pytest

from soniq import Soniq


@pytest.mark.asyncio
async def test_app_with_memory_backend():
    app = Soniq(backend="memory")

    @app.job(name="my_task")
    async def my_task(x: int):
        return x * 2

    job_id = await app.enqueue("my_task", args={"x": 10})
    assert job_id is not None

    status = await app.get_job(job_id)
    assert status["status"] == "queued"

    await app.close()


@pytest.mark.asyncio
async def test_app_close_is_idempotent():
    app = Soniq(backend="memory")
    await app.close()
    await app.close()  # Should not raise


@pytest.mark.asyncio
async def test_app_as_context_manager():
    async with Soniq(backend="memory") as app:

        @app.job(name="my_task")
        async def my_task():
            pass

        job_id = await app.enqueue("my_task")
        assert job_id is not None


@pytest.mark.asyncio
async def test_enqueue_unregistered_job_raises():
    from soniq.errors import SONIQ_UNKNOWN_TASK_NAME, SoniqError

    app = Soniq(backend="memory")

    with pytest.raises(SoniqError) as exc_info:
        await app.enqueue("not_registered")
    assert exc_info.value.error_code == SONIQ_UNKNOWN_TASK_NAME

    await app.close()


@pytest.mark.asyncio
async def test_schedule_delegates_to_enqueue():
    from datetime import datetime, timedelta, timezone

    app = Soniq(backend="memory")

    @app.job(name="my_task")
    async def my_task(msg: str):
        pass

    run_at = datetime.now(timezone.utc) + timedelta(hours=1)
    job_id = await app.schedule("my_task", args={"msg": "hello"}, run_at=run_at)
    assert job_id is not None

    status = await app.get_job(job_id)
    assert status["status"] == "queued"

    await app.close()


@pytest.mark.asyncio
async def test_cancel_job():
    app = Soniq(backend="memory")

    @app.job(name="my_task")
    async def my_task():
        pass

    job_id = await app.enqueue("my_task")
    result = await app.cancel_job(job_id)
    assert result is True

    status = await app.get_job(job_id)
    assert status["status"] == "cancelled"

    await app.close()


@pytest.mark.asyncio
async def test_delete_job():
    app = Soniq(backend="memory")

    @app.job(name="my_task")
    async def my_task():
        pass

    job_id = await app.enqueue("my_task")
    result = await app.delete_job(job_id)
    assert result is True

    status = await app.get_job(job_id)
    assert status is None

    await app.close()


@pytest.mark.asyncio
async def test_list_jobs_with_status_filter():
    app = Soniq(backend="memory")

    @app.job(name="my_task")
    async def my_task():
        pass

    await app.enqueue("my_task")
    await app.enqueue("my_task")

    jobs = await app.list_jobs(status="queued")
    assert len(jobs) == 2

    await app.close()


@pytest.mark.asyncio
async def test_get_queue_stats():
    app = Soniq(backend="memory")

    @app.job(name="my_task")
    async def my_task():
        pass

    await app.enqueue("my_task")
    stats = await app.get_queue_stats()
    assert stats["queued"] == 1
    assert stats["total"] == 1
    assert stats["dead_letter"] == 0

    await app.close()


@pytest.mark.asyncio
async def test_hook_registration():
    app = Soniq(backend="memory")
    before_calls = []
    after_calls = []
    error_calls = []

    @app.before_job
    async def on_before(job_name, job_id, attempt):
        before_calls.append(job_name)

    @app.after_job
    async def on_after(job_name, job_id, duration):
        after_calls.append(job_name)

    @app.on_error
    async def on_error(job_name, job_id, error, attempt):
        error_calls.append(error)

    @app.job(name="my_task")
    async def my_task():
        pass

    await app.enqueue("my_task")
    await app.run_worker(run_once=True)

    assert len(before_calls) == 1
    assert len(after_calls) == 1
    assert len(error_calls) == 0

    await app.close()


@pytest.mark.asyncio
async def test_run_worker_processes_jobs():
    app = Soniq(backend="memory")
    results = []

    @app.job(name="accumulate")
    async def accumulate(val: str):
        results.append(val)

    await app.enqueue("accumulate", args={"val": "a"})
    await app.enqueue("accumulate", args={"val": "b"})
    await app.run_worker(run_once=True)

    assert sorted(results) == ["a", "b"]
    await app.close()
