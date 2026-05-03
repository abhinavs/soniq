# Recipe: File Processing

A pattern for processing uploaded files (images, PDFs, videos) with long timeouts and CPU-bound work offloaded from the async event loop.

## The job

```python
import asyncio
from soniq import Soniq

eq = Soniq(database_url="postgresql://localhost/myapp")


@eq.job(queue="media", timeout=600, max_retries=2)
async def process_upload(file_id: int):
    file = await get_file(file_id)

    # Run CPU-bound work in a thread to avoid blocking the event loop
    thumbnail = await asyncio.to_thread(generate_thumbnail, file.path)
    await save_thumbnail(file_id, thumbnail)

    # Update status so the API can report progress
    await mark_file_processed(file_id)
```

## Enqueuing

```python
@app.post("/uploads")
async def upload_file(file: UploadFile):
    saved = await save_upload(file)
    await eq.enqueue(process_upload, file_id=saved.id)
    return {"file_id": saved.id, "status": "processing"}
```

## Why this works

**Long timeout.** `timeout=600` gives the job 10 minutes. Image resizing and video transcoding can take a while, and the default timeout is usually too short. Set this to match the worst-case processing time for your largest files.

**`asyncio.to_thread` for CPU work.** `generate_thumbnail` is CPU-bound (PIL, ffmpeg, etc.). Wrapping it in `asyncio.to_thread()` runs it in a thread pool so the worker's event loop stays responsive for heartbeats and other jobs.

**Limited retries.** File processing failures are usually deterministic (corrupt file, unsupported format). Retrying 10 times won't help. Two retries catches transient issues (disk full, OOM) without wasting resources on hopeless jobs.

**Dedicated queue.** Media processing is slow and resource-intensive. A separate `"media"` queue lets you control concurrency independently. Run one or two workers for media while your main queue handles fast jobs at higher concurrency.

## Running the worker

```bash
soniq worker --queues media --concurrency 1
```

Keep concurrency low for media workers. Each job may consume significant memory and CPU. Scale by adding more worker processes rather than increasing concurrency per process.

## Variations

**Multiple processing steps.** Break large jobs into a pipeline:

```python
@eq.job(queue="media", timeout=300, max_retries=2)
async def generate_thumbnails(file_id: int):
    file = await get_file(file_id)
    sizes = [(150, 150), (300, 300), (800, 800)]
    for width, height in sizes:
        thumb = await asyncio.to_thread(resize_image, file.path, width, height)
        await save_thumbnail(file_id, thumb, f"{width}x{height}")
    await eq.enqueue(extract_metadata, file_id=file_id)


@eq.job(queue="media", timeout=60, max_retries=2)
async def extract_metadata(file_id: int):
    file = await get_file(file_id)
    metadata = await asyncio.to_thread(read_exif, file.path)
    await save_metadata(file_id, metadata)
```

**Progress tracking.** Update a status field so the API can report progress:

```python
@eq.job(queue="media", timeout=600, max_retries=2)
async def process_video(file_id: int):
    await update_status(file_id, "transcoding")
    output = await asyncio.to_thread(transcode_video, file.path)

    await update_status(file_id, "generating_thumbnail")
    thumb = await asyncio.to_thread(extract_frame, output, seconds=5)

    await save_results(file_id, video_path=output, thumbnail=thumb)
    await update_status(file_id, "done")
```
