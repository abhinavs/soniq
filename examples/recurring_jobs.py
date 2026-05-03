"""Recurring jobs example.

Runs on a plain `pip install soniq` - `croniter` is a default dependency.

Start the scheduler process:
  soniq scheduler
"""

import asyncio
import os
from datetime import timedelta

from soniq import Soniq, cron, daily, every

app = Soniq(
    database_url=os.environ.get("SONIQ_DATABASE_URL", "postgresql://localhost/myapp")
)


@app.periodic(cron=daily().at("09:00"), name="reports.daily")
async def daily_report():
    print("Generating daily report")


@app.periodic(cron=every(10).minutes(), queue="maintenance", name="cleanup")
async def cleanup():
    print("Running cleanup")


@app.periodic(every=timedelta(seconds=30), name="metrics.flush")
async def flush_metrics():
    print("Flushing metrics")


@app.periodic(cron=cron("*/15 * * * *"), name="health.check")
async def health_check():
    print("Health check")


async def main() -> None:
    @app.job(name="ad_hoc_task")
    async def ad_hoc_task():
        print("Ad hoc")

    await app.scheduler.add(ad_hoc_task, cron=every(5).minutes())
    await app.scheduler.add(
        ad_hoc_task,
        cron="*/30 * * * *",
        queue="reports",
        priority=10,
    )


if __name__ == "__main__":
    asyncio.run(main())
