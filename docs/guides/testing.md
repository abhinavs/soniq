---
description: Test Soniq background jobs with pytest - the memory backend, soniq_memory and soniq_pg fixtures, drain, wait_until, retries and dead-letter jobs.
---

# Test Python background jobs with pytest and Soniq

Soniq ships a memory backend, pytest fixtures and helpers so you can test background jobs without a running PostgreSQL server. This page shows unit tests, round-trip tests, retry tests and integration tests. All examples use pytest.

## The `soniq.testing` package

Use `soniq.testing` in tests, examples and quick scripts only. It is separate from the production imports so its scope is clear at the import site:

```python
from soniq.testing import MemoryBackend, make_app, wait_until
```

- `MemoryBackend`: in-memory `StorageBackend`. No persistence, no concurrency contention. Use it for unit tests. Do not use it in production.
- `make_app(**overrides)`: one-liner Soniq wired against `MemoryBackend`. Equivalent to `Soniq(backend="memory", **overrides)`.
- `drain(soniq, max_jobs=None)`: run queued jobs until none are ready, in the test's own event loop. Returns the list of job results.
- `wait_until(predicate, *, timeout=2.0, poll=0.01, message=None)`: deadline-based polling that replaces fixed `asyncio.sleep(...)` calls in async tests. The predicate can be sync or async. Raises `AssertionError` on timeout.

## Pytest plugin

Soniq ships an opt-in pytest plugin. Soniq does not load it for you. Add this line to `conftest.py`:

```python
pytest_plugins = ["soniq.testing.pytest_plugin"]
```

You can also run `pytest -p soniq.testing.pytest_plugin`.

Turn on `asyncio_mode = "auto"` so async tests run without a marker on each test:

```toml
# pyproject.toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

The plugin gives you two fixtures:

- `soniq_memory`: a `Soniq` on the in-memory backend.
- `soniq_pg`: a `Soniq` on PostgreSQL. It reads `SONIQ_DATABASE_URL` and skips the test if the variable is not set. The test runs in one transaction. The plugin rolls the transaction back at the end, so the test leaves no rows behind. The plugin creates the Soniq tables first if they do not exist.

    Point `SONIQ_DATABASE_URL` at a dedicated, empty test database. Draining in a `soniq_pg` test runs every queued job in that database, including leftover jobs from other runs, with their real side effects.

To use the jobs of your own app, point the fixture at it with the `soniq` marker:

```python
from soniq.testing import drain

@pytest.mark.soniq(app="myapp.jobs:soniq")
async def test_welcome_email(soniq_memory):
    from myapp.jobs import send_email

    await soniq_memory.enqueue(send_email, to="dev@example.com")
    assert await drain(soniq_memory) == [{"sent": True}]
```

`drain` runs every ready job and returns the results of the jobs that finished. Jobs scheduled for later stay in the queue.

The marker also points your instance's `enqueue` and `enqueue_many` at the fixture for the length of the test. So `send_email.enqueue(...)`, and app code that calls `soniq.enqueue(...)` on your module-level instance, put jobs in the fixture's queue and not in your real database.

Async tests need `pytest-asyncio`. Set `asyncio_mode = "auto"` in your pytest config, or mark each test with `@pytest.mark.asyncio`. Without one of them, pytest fails each async test with "async def functions are not natively supported", because the default `strict` mode runs only marked tests.

## Memory backend for unit tests

The memory backend is the fastest way to test. It needs no external service and no cleanup script. State lives in Python dicts and disappears when the process exits:

```python
from soniq.testing import make_app

soniq = make_app()
```

(or, equivalently, `from soniq import Soniq; Soniq(backend="memory")`)

## Pytest fixture

This fixture gives each test a clean Soniq instance:

```python
import pytest
from soniq.testing import make_app

@pytest.fixture
async def soniq():
    soniq = make_app()
    yield soniq
    await soniq._reset()
    await soniq.close()
```

## Async waits without fixed sleeps

A fixed `asyncio.sleep(0.5)` in a test is a guess. It is too short on slow CI and too long on fast hardware. Use `wait_until` to poll a condition with a deadline:

```python
import asyncio
from soniq.testing import wait_until


@pytest.mark.asyncio
async def test_worker_completes_job(soniq):
    @soniq.job(name="jobs.do_thing")
    async def do_thing():
        return "done"

    job_id = await soniq.enqueue(do_thing)
    asyncio.create_task(soniq.run_worker(run_once=True))

    async def is_done():
        job = await soniq.get_job(job_id)
        return job and job["status"] == "done"

    await wait_until(
        is_done,
        timeout=2.0,
        message="job did not complete within 2s",
    )
```


## Testing job logic directly

Job functions are regular async functions. Call them without enqueuing:

```python
@soniq.job(name="emails.send_welcome", queue="emails")
async def send_welcome(to: str):
    return f"sent to {to}"

# Call the function directly. No queue is involved.
result = await send_welcome("alice@example.com")
assert result == "sent to alice@example.com"
```

Direct calls are the fastest way to test business logic. Use round-trip tests for integration coverage.

## Enqueue + process round-trips

Call `run_worker(run_once=True)` to run the queued jobs inside your test:

```python
async def test_round_trip(soniq):
    results = []

    @soniq.job(name="jobs.track_call", queue="default")
    async def track_call(value: str):
        results.append(value)

    await soniq.enqueue(track_call, value="hello")
    await soniq.run_worker(run_once=True)

    assert results == ["hello"]
```

`run_once=True` runs all ready jobs and then returns. It needs no polling loop and no sleep.

## Checking job status

```python
async def test_job_status(soniq):
    @soniq.job(name="jobs.noop")
    async def noop():
        pass

    job_id = await soniq.enqueue(noop)
    job = await soniq.get_job(job_id)
    assert job["status"] == "queued"
```

## Testing retries

Enqueue a job that raises. Then call `run_worker(run_once=True)` once for each attempt. With `retries=2`, a job has 3 attempts:

```python
async def test_retry_behavior(soniq):
    attempts = []

    @soniq.job(name="jobs.flaky_job", retries=2, retry_delay=0)
    async def flaky_job():
        attempts.append(1)
        if len(attempts) < 3:
            raise RuntimeError("not yet")

    await soniq.enqueue(flaky_job)
    await soniq.run_worker(run_once=True)  # attempt 1: fails
    await soniq.run_worker(run_once=True)  # attempt 2: fails
    await soniq.run_worker(run_once=True)  # attempt 3: succeeds

    assert len(attempts) == 3
```

## Testing failed jobs

A job that uses all its retries moves to the dead-letter table, not `soniq_jobs`. Read it through `soniq.dead_letter`. The dead-letter table is in PostgreSQL, so use the `soniq_pg` fixture:

```python
async def test_failure_tracking(soniq_pg):
    @soniq_pg.job(name="jobs.always_fails", retries=0)
    async def always_fails():
        raise ValueError("boom")

    await soniq_pg.enqueue(always_fails)
    await soniq_pg.run_worker(run_once=True)

    dead = await soniq_pg.dead_letter.list_dead_letter_jobs()
    assert len(dead) == 1
```

## Resetting state between tests

If tests share one instance, for example in a session-scoped fixture, call `_reset()` to delete all jobs and workers:

```python
@pytest.fixture(autouse=True)
async def clean_slate(soniq):
    yield
    await soniq._reset()
```

`_reset()` deletes the job and worker rows, or clears the in-memory dicts. It keeps the connection open.

## SQLite for integration tests

Use SQLite when you need a real SQL database in a test but do not want to run PostgreSQL in CI:

```bash
pip install "soniq[sqlite]"
```

The base `pip install soniq` does not install the SQLite driver. Without the extra, `Soniq(backend="sqlite", ...)` raises an `ImportError` that names `soniq[sqlite]`.

```python
import pytest
from soniq import Soniq

@pytest.fixture
async def soniq(tmp_path):
    db_path = str(tmp_path / "test.db")
    soniq = Soniq(backend="sqlite", database_url=db_path)
    yield soniq
    await soniq.close()
```

Pass a **bare file path** (`"test.db"`, `str(tmp_path / "test.db")`), not a
`sqlite:///` URL. Soniq also accepts the `sqlite:///relative.db` /
`sqlite:////abs/path.db` URL form and strips the scheme, but the bare path is
the form the rest of the docs use. The same goes for the memory backend:
`Soniq(backend="memory")`, no URL.

SQLite gives you real SQL behaviour (constraints, transactions) with no external service. Use `tmp_path` so each test gets a fresh database file that pytest removes later.

## Tips

- Keep unit tests on the Memory backend. Reserve PostgreSQL tests for CI or a dedicated integration suite.
- Use `run_once=True`. It is deterministic and fast.
- `JobContext` is injected automatically. Add a `ctx: JobContext` parameter to your job function to inspect context during tests.
- To test hooks, register `@soniq.before_job`, `@soniq.after_job` or `@soniq.on_error` and assert on the positional arguments they receive. See [Common patterns](common-patterns.md#job-hooks-run-code-before-after-and-on-error).

## Next steps

- [Common patterns](common-patterns.md) - hooks, deduplication and job results
- [FastAPI integration](fastapi.md) - test routes that enqueue jobs
