# Testing

Practical patterns for testing applications that use Soniq. All examples use pytest.

## The `soniq.testing` package

Everything in `soniq.testing` is for tests, examples, and quick scripts. It is intentionally separate from production-tier imports so its scope is obvious at the import site:

```python
from soniq.testing import MemoryBackend, make_app, wait_until
```

- `MemoryBackend`: in-memory `StorageBackend`. No persistence, no concurrency contention. Good for unit tests; never use it in production.
- `make_app(**overrides)`: one-liner Soniq wired against `MemoryBackend`. Equivalent to `Soniq(backend="memory", **overrides)`.
- `drain(soniq, max_jobs=None)`: run queued jobs until none are ready, in the test's own event loop. Returns the list of job results.
- `wait_until(predicate, *, timeout=2.0, poll=0.01, message=None)`: deadline-based polling that replaces fixed `asyncio.sleep(...)` calls in async tests. Predicate may be sync or async. Raises `AssertionError` on timeout.

## Pytest plugin

Soniq ships an opt-in pytest plugin. Soniq does not load it for you. Add this line to your `conftest.py`:

```python
pytest_plugins = ["soniq.testing.pytest_plugin"]
```

You can also run `pytest -p soniq.testing.pytest_plugin`.

The plugin gives you two fixtures:

- `soniq_memory`: a `Soniq` on the in-memory backend.
- `soniq_pg`: a `Soniq` on PostgreSQL. It reads `SONIQ_DATABASE_URL` and skips the test if the variable is not set. The test runs in one transaction. The plugin rolls it back at the end, so the test leaves no rows behind. It creates the Soniq tables first if they do not exist.

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

The async tests need `pytest-asyncio`.

## Memory backend for unit tests

The fastest way to test. No external services, no cleanup scripts. State lives in Python dicts and disappears when the process exits:

```python
from soniq.testing import make_app

soniq = make_app()
```

(or, equivalently, `from soniq import Soniq; Soniq(backend="memory")`)

## Pytest fixture

A reusable fixture that gives each test a clean, isolated Soniq instance:

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

`asyncio.sleep(0.5)` in a test is a guess: too short on slow CI, too long on fast hardware. Use `wait_until` to poll an observable condition with a deadline:

```python
import asyncio
from soniq.testing import wait_until


@pytest.mark.asyncio
async def test_worker_completes_job(soniq):
    @soniq.job()
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

Every test that accepts `soniq` gets its own queue with no leftover state from previous runs.

## Testing job logic directly

Job functions are regular async functions. Call them without enqueuing:

```python
@soniq.job(queue="emails")
async def send_welcome(to: str):
    return f"sent to {to}"

# Call directly -- no queue involved
result = await send_welcome("alice@example.com")
assert result == "sent to alice@example.com"
```

This is the fastest way to test business logic. Save round-trip tests for integration coverage.

## Enqueue + process round-trips

Use `run_worker(run_once=True)` to drain the queue synchronously in your test:

```python
async def test_round_trip(soniq):
    results = []

    @soniq.job(queue="default")
    async def track_call(value: str):
        results.append(value)

    await soniq.enqueue(track_call, value="hello")
    await soniq.run_worker(run_once=True)

    assert results == ["hello"]
```

`run_once=True` processes all available jobs and returns immediately. No polling loop, no timeouts, no flaky sleeps.

## Checking job status

```python
async def test_job_status(soniq):
    @soniq.job()
    async def noop():
        pass

    job_id = await soniq.enqueue(noop)
    job = await soniq.get_job(job_id)
    assert job["status"] == "queued"
```

## Testing retries

Enqueue a job that raises, then call `run_worker(run_once=True)` multiple times:

```python
async def test_retry_behavior(soniq):
    attempts = []

    @soniq.job(max_retries=2, retry_delay=0)
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

Jobs that exhaust retries land in the dead-letter table, not in
`soniq_jobs`. Inspect them through `soniq.dead_letter`:

```python
async def test_failure_tracking(soniq):
    @soniq.job(max_retries=0)
    async def always_fails():
        raise ValueError("boom")

    await soniq.enqueue(always_fails)
    await soniq.run_worker(run_once=True)

    dead = await soniq.dead_letter.list_dead_letter_jobs()
    assert len(dead) == 1
```

## Resetting state between tests

If you share a single instance across tests (for example, a session-scoped fixture), call `_reset()` to wipe all jobs and workers:

```python
@pytest.fixture(autouse=True)
async def clean_slate(soniq):
    yield
    await soniq._reset()
```

`_reset()` truncates job and worker tables (or clears the in-memory dicts) without tearing down the connection.

## SQLite for integration tests

When you need to test against a real SQL database but don't want to run PostgreSQL in CI:

```bash
pip install soniq[sqlite]
```

The base `pip install soniq` does not pull in the SQLite driver. Without the
extra, `Soniq(backend="sqlite", ...)` raises an `ImportError` naming
`soniq[sqlite]`.

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

SQLite gives you real SQL semantics (constraints, transactions) without external dependencies. Use `tmp_path` so each test gets a fresh database file that's automatically cleaned up.

## Tips

- Keep unit tests on the Memory backend. Reserve PostgreSQL tests for CI or a dedicated integration suite.
- Use `run_once=True` liberally. It's deterministic and fast.
- `JobContext` is injected automatically. Add a `ctx: JobContext` parameter to your job function to inspect context during tests.
- For hooks testing, register `@soniq.before_job` / `@soniq.after_job` / `@soniq.on_error` and assert they were called with the expected context.
