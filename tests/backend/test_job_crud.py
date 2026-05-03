"""
Backend conformance: job CRUD operations.
"""


async def test_create_and_get_job(backend):
    job_id = await backend.create_job(
        job_id="test-1",
        job_name="mod.func",
        args={"x": 1},
        args_hash=None,
        max_attempts=3,
        priority=100,
        queue="default",
        unique=False,
        scheduled_at=None,
    )
    assert job_id == "test-1"

    job = await backend.get_job("test-1")
    assert job is not None
    assert job["status"] == "queued"
    assert job["job_name"] == "mod.func"
    assert job["args"] == {"x": 1}


async def test_get_nonexistent_returns_none(backend):
    job = await backend.get_job("nonexistent")
    assert job is None


async def test_list_jobs_empty(backend):
    jobs = await backend.list_jobs()
    assert jobs == []


async def test_list_jobs_with_data(backend):
    await backend.create_job(
        job_id="j1",
        job_name="mod.a",
        args={},
        args_hash=None,
        max_attempts=3,
        priority=100,
        queue="default",
        unique=False,
    )
    await backend.create_job(
        job_id="j2",
        job_name="mod.b",
        args={},
        args_hash=None,
        max_attempts=3,
        priority=100,
        queue="emails",
        unique=False,
    )

    all_jobs = await backend.list_jobs()
    assert len(all_jobs) == 2

    default_jobs = await backend.list_jobs(queue="default")
    assert len(default_jobs) == 1
    assert default_jobs[0]["job_name"] == "mod.a"


async def test_list_jobs_filtered_by_status(backend):
    await backend.create_job(
        job_id="j1",
        job_name="mod.a",
        args={},
        args_hash=None,
        max_attempts=3,
        priority=100,
        queue="default",
        unique=False,
    )
    await backend.cancel_job("j1")

    queued = await backend.list_jobs(status="queued")
    assert len(queued) == 0

    cancelled = await backend.list_jobs(status="cancelled")
    assert len(cancelled) == 1


async def test_delete_job(backend):
    await backend.create_job(
        job_id="j1",
        job_name="mod.func",
        args={},
        args_hash=None,
        max_attempts=3,
        priority=100,
        queue="default",
        unique=False,
    )
    assert await backend.delete_job("j1") is True
    assert await backend.get_job("j1") is None


async def test_delete_nonexistent_returns_false(backend):
    assert await backend.delete_job("nonexistent") is False
