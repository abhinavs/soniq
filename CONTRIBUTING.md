# Contributing to Soniq

Thanks for contributing. Soniq is a **single package** with optional extras for advanced features.

## Project Structure

- `soniq/` — core runtime
- `soniq/dashboard/` — web UI (optional, opt-in)
- `soniq/features/` — scheduling, recurring, metrics, logging, webhooks, dead-letter, security (optional, opt-in)

## Getting Started

```bash
git clone https://github.com/abhinavs/soniq.git
cd soniq

python -m venv venv
source venv/bin/activate

pip install -e ".[dev]"
```

`.[dev]` is batteries-included for the test harness: it pulls in
FastAPI, aiohttp, aiosqlite, structlog, and cryptography on top of the
test runners, so you do not need any additional extras to run the
suite.

## Database Setup

Create a test database and set the connection string:

```bash
createdb soniq_test
export SONIQ_DATABASE_URL="postgresql://localhost/soniq_test"
```

Run migrations:

```bash
python -c "
import asyncio
from soniq.db.migrations import run_migrations
asyncio.run(run_migrations())
"
```

## Running Tests

The test suite is organized into tiers, from fastest (no dependencies) to slowest (requires Postgres):

**Unit tests** — MemoryBackend, no database needed:

```bash
python -m pytest tests/unit/ -v
```

**Backend conformance tests** — runs against Memory and SQLite backends to verify protocol compliance:

```bash
python -m pytest tests/backend/ -v
```

**Functional tests** — SQLite backend, no Postgres needed:

```bash
python -m pytest tests/functional/ -v
```

**Integration tests** — requires the Postgres test database above:

```bash
python -m pytest tests/integration/ -v
```

**Smoke tests** — quick sanity checks against the example code:

```bash
python -m pytest tests/smoke/ -v
```

**Everything except integration** (good for local development):

```bash
python -m pytest tests/unit tests/backend tests/functional tests/smoke -v
```

**Full suite with coverage:**

```bash
python -m pytest tests/ --cov=soniq --cov-report=term-missing -v
```

## Coding Standards

- Use async/await consistently
- Add type hints for public APIs
- Keep error messages actionable
- Add tests for new behavior

## Releasing

Releases are cut by pushing a `v*` tag. The `publish.yml` workflow verifies the tag matches `pyproject.toml:project.version`, builds the sdist + wheel, publishes to PyPI via OIDC, and creates the GitHub Release.

```bash
# 1. Bump version in pyproject.toml and CHANGELOG.md, commit, push to main
# 2. Tag and push:
git tag v0.0.2
git push origin v0.0.2
```

That is the only supported release path; there is no local publish script.
