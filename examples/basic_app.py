import asyncio
import os

from soniq import Soniq

soniq = Soniq(
    database_url=os.environ.get("SONIQ_DATABASE_URL", "postgresql://localhost/myapp")
)


@soniq.job(name="process_upload")
async def process_upload(file_path: str):
    return f"Processed {file_path}"


async def main():
    await soniq.setup()
    job_id = await soniq.enqueue(
        "process_upload", args={"file_path": "/uploads/photo.jpg"}
    )
    print(f"Enqueued job: {job_id}")
    await soniq.run_worker(run_once=True)
    await soniq.close()


if __name__ == "__main__":
    asyncio.run(main())
