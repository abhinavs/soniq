"""File processing example.

Keep payloads small: store files elsewhere and pass IDs/paths.
"""

import asyncio
import os

from soniq import Soniq

soniq = Soniq(
    database_url=os.environ.get("SONIQ_DATABASE_URL", "postgresql://localhost/myapp")
)


@soniq.job(name="process_file", retries=3, retry_backoff=True, retry_delay=2)
async def process_file(file_id: str, storage_path: str):
    print(f"Processing file {file_id} at {storage_path}")


async def main() -> None:
    await soniq.setup()
    await soniq.enqueue(
        "process_file",
        args={"file_id": "file_abc", "storage_path": "/tmp/uploads/file_abc.bin"},
    )
    await soniq.run_worker(run_once=True)
    await soniq.close()


if __name__ == "__main__":
    asyncio.run(main())
