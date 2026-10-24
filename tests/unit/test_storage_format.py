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


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [math.nan, math.inf])
async def test_enqueue_rejects_nan_and_infinity(bad):
    app = Soniq(backend="memory")

    @app.job(name="format.number")
    async def handler(n: float):
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


def test_args_hash_matches_the_documented_vector():
    # Keep in sync with docs/reference/storage-format.md.
    args = {"to": "zoë@example.com", "order_id": 42, "tags": ["a", "b"]}
    assert (
        compute_args_hash(args)
        == "4679a46d38de4bf4d70793166c09d90fb32fa2f36cac5f9193000b4e17bc2c9c"
    )
