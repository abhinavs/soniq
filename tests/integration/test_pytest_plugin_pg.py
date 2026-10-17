"""The ``soniq_pg`` fixture from the opt-in pytest plugin, run through pytester."""

import os

import pytest

pytest_plugins = ["pytester"]


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
