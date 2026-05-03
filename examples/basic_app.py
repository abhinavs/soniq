import asyncio
import os

from soniq import Soniq

app = Soniq(
    database_url=os.environ.get("SONIQ_DATABASE_URL", "postgresql://localhost/myapp")
)


@app.job(name="process_upload")
async def process_upload(file_path: str):
    return f"Processed {file_path}"


async def main():
    await app.setup()
    job_id = await app.enqueue(
        "process_upload", args={"file_path": "/uploads/photo.jpg"}
    )
    print(f"Enqueued job: {job_id}")
    await app.run_worker(run_once=True)
    await app.close()


if __name__ == "__main__":
    asyncio.run(main())
