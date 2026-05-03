"""Instance-based API example.

Shows Soniq used as an application object. Run with:
``python examples/instance_api.py``.
"""

import asyncio

from soniq import JobContext, Soniq

app = Soniq(database_url="postgresql://localhost/myapp")


@app.job(name="send_notification", queue="notifications", retries=2)
async def send_notification(user_id: int, message: str, ctx: JobContext):
    print(f"[job {ctx.job_id}, attempt {ctx.attempt}] notify user {user_id}: {message}")


@app.job(name="generate_report", queue="reports")
async def generate_report(report_type: str):
    print(f"Generating {report_type} report")


async def main():
    # Enqueue a couple of jobs
    job_id = await app.enqueue(
        "send_notification", args={"user_id": 42, "message": "Welcome aboard"}
    )
    print(f"Enqueued notification: {job_id}")

    await app.enqueue("generate_report", args={"report_type": "monthly"})

    # Process all queued jobs and exit
    await app.run_worker(run_once=True, queues=["notifications", "reports"])

    await app.close()


if __name__ == "__main__":
    asyncio.run(main())
