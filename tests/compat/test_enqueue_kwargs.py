"""Job arguments named like common words must reach the job unchanged.

``enqueue(job, **kwargs)`` passes every keyword it does not reserve to the
job. Jobs in the wild use names such as ``session``, ``app`` or ``db``. If
``enqueue()`` ever starts to reserve one of these names, those jobs break.
"""

from soniq import Soniq

ARGS = {
    "session": "sess-1",
    "app": "billing",
    "options": {"retry": False, "tags": ["a", "b"]},
    "soniq": 3,
    "db": "primary",
    "context": None,
    "job": "nightly",
    "timeout": 12.5,
    "name": "n",
    "delay": 0,
}


async def test_common_word_kwargs_reach_the_job(fresh_db):
    soniq = Soniq(database_url=fresh_db("soniq_compat_kwargs"))
    received = []

    @soniq.job(name="compat.common_words")
    async def common_words(
        session, app, options, soniq, db, context, job, timeout, name, delay
    ):
        received.append(
            dict(
                session=session,
                app=app,
                options=options,
                soniq=soniq,
                db=db,
                context=context,
                job=job,
                timeout=timeout,
                name=name,
                delay=delay,
            )
        )

    try:
        await soniq.setup()
        job_id = await soniq.enqueue(common_words, **ARGS)
        stored = await soniq.get_job(job_id)
        await soniq.run_worker(run_once=True)
    finally:
        await soniq.close()

    assert stored["args"] == ARGS
    assert received == [ARGS]
