"""Queue routing example.

Run workers for specific queues:
  soniq worker --queues emails,media
"""

import asyncio
import os

from soniq import Soniq

app = Soniq(
    database_url=os.environ.get("SONIQ_DATABASE_URL", "postgresql://localhost/myapp")
)


@app.job(name="send_email", queue="emails")
async def send_email(to: str, subject: str):
    print(f"Sending email to {to}: {subject}")


@app.job(name="transcode_video", queue="media")
async def transcode_video(video_id: str):
    print(f"Transcoding video {video_id}")


async def main() -> None:
    await app.setup()
    await app.enqueue(
        "send_email", args={"to": "dev@example.com", "subject": "Welcome"}
    )
    await app.enqueue("transcode_video", args={"video_id": "vid_123"})
    await app.run_worker(run_once=True)
    await app.close()


if __name__ == "__main__":
    asyncio.run(main())
