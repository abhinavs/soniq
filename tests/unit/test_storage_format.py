"""The storage format rules from docs/reference/storage-format.md.

Rows must be plain JSON so code in any language can read them back.
"""

import logging
import math
import warnings
from datetime import datetime, timezone

import pytest

from soniq import Soniq, SoniqError
from soniq._deprecation import SoniqDeprecationWarning
from soniq.backends.helpers import to_json
from soniq.testing import drain
from soniq.utils.hashing import compute_args_hash


def test_to_json_rejects_objects_and_nan():
    assert to_json({"a": [1, "x", None, True]}) == '{"a": [1, "x", null, true]}'
    with pytest.raises(TypeError):
        to_json({"when": datetime.now()})
    for bad in (math.nan, math.inf, -math.inf):
        with pytest.raises(ValueError):
            to_json({"n": bad})


def test_to_json_rejects_what_postgres_cannot_store():
    for bad in ({"s": "a\x00b"}, {"a\x00": 1}, {"s": "\ud800"}):
        with pytest.raises(ValueError):
            to_json(bad)
    # A backslash followed by u0000 is ordinary text.
    assert to_json({"s": "\\u0000"}) == '{"s": "\\\\u0000"}'


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [math.nan, math.inf, "a\x00b", "\ud800"])
async def test_enqueue_rejects_nan_and_infinity(bad):
    app = Soniq(backend="memory")

    @app.job(name="format.number")
    async def handler(n):
        pass

    with pytest.raises(SoniqError) as exc:
        await app.enqueue(handler, n=bad)
    assert exc.value.error_code == "SONIQ_TASK_ARGS_INVALID"


@pytest.mark.asyncio
async def test_non_json_result_is_stored_as_null(caplog):
    app = Soniq(backend="memory")

    @app.job(name="format.clock")
    async def clock():
        return datetime(2024, 1, 1, tzinfo=timezone.utc)

    job_id = await app.enqueue(clock)
    with caplog.at_level(logging.ERROR):
        await drain(app)

    job = await app.get_job(job_id)
    assert job["status"] == "done"
    assert job["result"] is None
    assert "returned a datetime" in caplog.text
    await app.close()


@pytest.mark.asyncio
async def test_unstorable_result_and_error_text(caplog):
    app = Soniq(backend="memory")

    @app.job(name="format.surrogate")
    async def surrogate():
        return {"s": "\ud800"}

    @app.job(name="format.nul_error", retries=0)
    async def nul_error():
        raise RuntimeError("bad\x00byte \udcff")

    ok_id = await app.enqueue(surrogate)
    bad_id = await app.enqueue(nul_error)
    with caplog.at_level(logging.ERROR):
        await drain(app)

    assert (await app.get_job(ok_id))["result"] is None
    assert "can't be stored" in caplog.text
    dead = await app.get_job(bad_id)
    assert dead["status"] == "dead_letter"
    assert "\x00" not in dead["last_error"]
    dead["last_error"].encode("utf-8")
    await app.close()


def test_derived_name_that_fails_the_pattern_says_what_to_do():
    app = Soniq(backend="memory")

    def sendEmail():
        pass

    with pytest.warns(SoniqDeprecationWarning, match="SONIQ_TASK_NAME_PATTERN"):
        app.job()(sendEmail)


def test_derived_job_name_warns_and_explicit_name_does_not():
    app = Soniq(backend="memory")

    with pytest.warns(SoniqDeprecationWarning, match="name="):

        @app.job()
        async def derived_name_job():
            pass

    with warnings.catch_warnings():
        warnings.simplefilter("error", SoniqDeprecationWarning)

        @app.job(name="format.explicit")
        async def explicit_name_job():
            pass


def test_nameless_periodic_job_warns_once_from_the_users_file():
    app = Soniq(backend="memory")

    with pytest.warns(SoniqDeprecationWarning, match="periodic") as caught:

        @app.periodic(every=60)
        async def derived_periodic_job():
            pass

    assert len(caught) == 1
    assert caught[0].filename == __file__


def test_args_hash_matches_the_documented_vector():
    # Keep in sync with docs/reference/storage-format.md.
    args = {"to": "zoë@example.com", "order_id": 42, "tags": ["a", "b"]}
    assert (
        compute_args_hash(args)
        == "4679a46d38de4bf4d70793166c09d90fb32fa2f36cac5f9193000b4e17bc2c9c"
    )
