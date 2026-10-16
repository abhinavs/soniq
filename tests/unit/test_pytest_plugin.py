"""Tests for the opt-in pytest plugin, run through pytester."""

import os

import pytest

pytest_plugins = ["pytester"]

JOBS = """
from soniq import Soniq

app = Soniq(backend="memory")

@app.job()
async def double(n: int):
    return n * 2
"""

TEST = """
import pytest
from soniq.testing.helpers import drain

@pytest.mark.soniq(app="myjobs:app")
async def test_drain(soniq_memory):
    from myjobs import double
    await soniq_memory.enqueue(double, n=2)
    await soniq_memory.enqueue(double, n=5)
    assert sorted(await drain(soniq_memory)) == [4, 10]
    assert await drain(soniq_memory) == []

async def test_inline_job(soniq_memory):
    @soniq_memory.job()
    async def one():
        return 1
    for _ in range(3):
        await soniq_memory.enqueue(one)
    assert await drain(soniq_memory, max_jobs=2) == [1, 1]
    assert await drain(soniq_memory) == [1]
"""


def _setup(pytester, conftest):
    pytester.makeini("[pytest]\nasyncio_mode = auto\n")
    pytester.makepyfile(myjobs=JOBS)
    pytester.makeconftest(conftest)
    pytester.makepyfile(test_it=TEST)


def test_opt_in_runs_drain(pytester):
    _setup(pytester, 'pytest_plugins = ["soniq.testing.pytest_plugin"]')
    pytester.runpytest_subprocess().assert_outcomes(passed=2)


def test_not_loaded_without_opt_in(pytester):
    _setup(pytester, "")
    result = pytester.runpytest_subprocess()
    result.assert_outcomes(errors=2)
    result.stdout.fnmatch_lines(["*fixture 'soniq_memory' not found*"])


@pytest.mark.skipif(
    not os.environ.get("SONIQ_DATABASE_URL"), reason="needs SONIQ_DATABASE_URL"
)
def test_pg_rolls_back(pytester):
    pytester.makeini("[pytest]\nasyncio_mode = auto\n")
    pytester.makeconftest('pytest_plugins = ["soniq.testing.pytest_plugin"]')
    pytester.makepyfile(
        test_pg="""
        from soniq.testing.helpers import drain

        # The database may hold rows from other tests, so track our own job.
        seen = []

        async def test_a(soniq_pg):
            @soniq_pg.job()
            async def hi():
                return "hi"
            seen.append(await soniq_pg.enqueue(hi))
            assert await drain(soniq_pg) == ["hi"]
            assert await soniq_pg.get_job(seen[0]) is not None

        async def test_b(soniq_pg):
            assert await soniq_pg.get_job(seen[0]) is None
        """
    )
    pytester.runpytest_subprocess().assert_outcomes(passed=2)
