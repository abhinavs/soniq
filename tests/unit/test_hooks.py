"""Test job middleware hooks."""

from soniq import Soniq


async def test_before_and_after_hooks_called():
    calls = []

    async with Soniq(backend="memory") as app:

        @app.before_job
        def on_before(job_name, job_id, attempt):
            calls.append(("before", job_name, attempt))

        @app.after_job
        def on_after(job_name, job_id, duration_ms):
            calls.append(("after", job_name))

        @app.job(name="my_job")
        async def my_job():
            pass

        await app.enqueue("my_job")
        await app.run_worker(run_once=True)

    before_calls = [c for c in calls if c[0] == "before"]
    after_calls = [c for c in calls if c[0] == "after"]
    assert len(before_calls) == 1
    assert len(after_calls) == 1
    assert before_calls[0][2] == 1  # attempt is 1 (first attempt)


async def test_on_error_hook_called():
    calls = []

    async with Soniq(backend="memory") as app:

        @app.on_error
        def on_err(job_name, job_id, error, attempt):
            calls.append(("error", error, attempt))

        @app.job(name="failing_job", max_retries=0)
        async def failing_job():
            raise RuntimeError("boom")

        await app.enqueue("failing_job")
        await app.run_worker(run_once=True)

    assert len(calls) == 1
    assert "boom" in calls[0][1]
    assert calls[0][2] == 1


async def test_async_hooks_supported():
    calls = []

    async with Soniq(backend="memory") as app:

        @app.before_job
        async def on_before(job_name, job_id, attempt):
            calls.append("async_before")

        @app.job(name="my_job")
        async def my_job():
            pass

        await app.enqueue("my_job")
        await app.run_worker(run_once=True)

    assert "async_before" in calls


async def test_broken_hook_does_not_kill_processing():
    executed = []

    async with Soniq(backend="memory") as app:

        @app.before_job
        def broken_hook(job_name, job_id, attempt):
            raise RuntimeError("hook crash")

        @app.job(name="my_job")
        async def my_job():
            executed.append(True)

        await app.enqueue("my_job")
        await app.run_worker(run_once=True)

    assert len(executed) == 1
