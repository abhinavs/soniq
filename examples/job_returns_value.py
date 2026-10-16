"""
Minimal demonstration that a job's return value is persisted and retrievable.

Run:
    SONIQ_DATABASE_URL=postgresql://postgres@localhost/soniq \\
        python examples/job_returns_value.py
"""

import asyncio
import os

from soniq import Soniq

soniq = Soniq(
    database_url=os.environ.get(
        "SONIQ_DATABASE_URL", "postgresql://postgres@localhost/soniq"
    )
)


@soniq.job(name="compute_summary")
async def compute_summary(a: int, b: int):
    return {"total": a + b, "inputs": [a, b]}


async def main():
    await soniq.setup()

    job_id = await soniq.enqueue("compute_summary", args={"a": 7, "b": 35})
    await soniq.run_worker(run_once=True)

    result = await soniq.get_result(job_id)
    print(f"job_id={job_id}")
    print(f"result={result}")
    assert result == {"total": 42, "inputs": [7, 35]}, f"unexpected result: {result}"

    await soniq.close()


if __name__ == "__main__":
    asyncio.run(main())
