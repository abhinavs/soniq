"""Test job result storage."""

from soniq import Soniq


async def test_job_result_stored():
    async with Soniq(backend="memory") as app:

        @app.job(name="compute")
        async def compute():
            return {"total": 42}

        job_id = await app.enqueue("compute")
        await app.run_worker(run_once=True)

        result = await app.get_result(job_id)
        assert result == {"total": 42}


async def test_job_result_none_for_pending():
    async with Soniq(backend="memory") as app:

        @app.job(name="noop")
        async def noop():
            pass

        job_id = await app.enqueue("noop")
        result = await app.get_result(job_id)
        assert result is None


async def test_job_result_in_get_job():
    async with Soniq(backend="memory") as app:

        @app.job(name="compute")
        async def compute():
            return {"total": 42}

        job_id = await app.enqueue("compute")
        await app.run_worker(run_once=True)

        job = await app.get_job(job_id)
        assert job["result"] == {"total": 42}


async def test_job_result_none_for_failed():
    async with Soniq(backend="memory") as app:

        @app.job(name="failing", max_retries=0)
        async def failing():
            raise RuntimeError("boom")

        job_id = await app.enqueue("failing")
        await app.run_worker(run_once=True)

        result = await app.get_result(job_id)
        assert result is None
