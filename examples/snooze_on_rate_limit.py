"""
Snooze a job when an upstream API returns HTTP 429 (Too Many Requests).

Returning `Snooze(seconds=N)` from a handler re-schedules the job N seconds
into the future without consuming a retry slot. This is the right pattern
for rate-limited APIs and webhook backpressure: a 429 is not a failure, just
a "come back later" signal, and counting it against max_attempts would
exhaust the retry budget for reasons that have nothing to do with the job
itself.
"""

import asyncio
import os

from soniq import Snooze, Soniq

app = Soniq(
    database_url=os.environ.get("SONIQ_DATABASE_URL", "postgresql://localhost/myapp")
)


@app.job(name="call_rate_limited_api", retries=3)
async def call_rate_limited_api(order_id: str):
    response = await _simulated_api_call(order_id)
    if response["status"] == 429:
        retry_after = int(response["headers"].get("Retry-After", 30))
        return Snooze(seconds=retry_after, reason=f"rate-limited on {order_id}")
    return {"order_id": order_id, "ok": True}


async def _simulated_api_call(order_id: str) -> dict:
    return {"status": 429, "headers": {"Retry-After": "5"}}


async def main():
    await app.setup()
    await app.enqueue("call_rate_limited_api", args={"order_id": "order-1"})
    await app.run_worker(concurrency=1)
    await app.close()


if __name__ == "__main__":
    asyncio.run(main())
