---
description: How the Soniq dead-letter queue works on PostgreSQL - why Python jobs land there, and how to list, replay, delete, clean up and export them from CLI or code.
---

# Dead-letter queue: inspect and replay failed jobs

The Soniq dead-letter queue (DLQ) holds jobs that failed for good. They move from `soniq_jobs` to a separate `soniq_dead_letter_jobs` table in PostgreSQL, where you can inspect, debug and replay them with the CLI or Python.

## Setup

The DLQ table is part of the core schema. Run `soniq setup` once to create
the schema. After that, Soniq moves any job that fails its final attempt
into `soniq_dead_letter_jobs` automatically.

## How jobs get there

A job enters the DLQ when:

- It has used all retry attempts and still fails (`max_retries_exceeded`). A job that times out on every attempt ends here too.
- The retry policy declines to retry the error (`permanent_failure`).
- The job function is not registered in the worker (`job_not_found`).
- The arguments fail validation (`invalid_arguments`).

Each dead-letter record keeps the original job name, arguments, queue, priority, error message, and attempt count.

## CLI management

### List dead-letter jobs

```bash
soniq dead-letter list
soniq dead-letter list --limit 20
soniq dead-letter list --filter "emails.send_welcome_email"
```

`--filter` matches the exact job name.

### Replay a job

Replay creates a new `soniq_jobs` row with the same function and arguments,
reset to `queued` status. The original DLQ row stays as the audit trail and
its `resurrection_count` is incremented:

```bash
soniq dead-letter replay <job-id> --jobs-modules myapp.jobs
```

The command needs your job modules loaded (`--jobs-modules`, `--app` or `SONIQ_JOBS_MODULES`). Soniq reads the job's retry limits from them.

### Delete a dead-letter job

```bash
soniq dead-letter delete <job-id>
```

### Clean up old entries

```bash
soniq dead-letter cleanup --days 30        # remove entries older than 30 days
soniq dead-letter cleanup --days 7 --dry-run  # preview what would be removed
```

### Export for analysis

```bash
soniq dead-letter export --format json --output dead_jobs.json
soniq dead-letter export --format csv --output dead_jobs.csv
```

## Programmatic API

The dead-letter API is reached through your `Soniq` instance:
`soniq.dead_letter.<method>()`. The handle is constructed lazily on first
access and cached on the instance. Full method and type reference:
[Dead-letter queue Python API](../api/dead-letter.md).

```python
from soniq import Soniq
from soniq.features.dead_letter import DeadLetterFilter

soniq = Soniq(database_url="postgresql://localhost/myapp")

# List all dead-letter jobs
jobs = await soniq.dead_letter.list_dead_letter_jobs()

# Get a specific job
job = await soniq.dead_letter.get_dead_letter_job("abc123-def456")

# Replay with options
new_job_id = await soniq.dead_letter.replay(
    "abc123-def456",
    reset_attempts=True,       # start fresh (default)
    new_max_attempts=10,       # give it more tries this time
    new_queue="retry-queue",   # route to a different queue
)

# Delete permanently
await soniq.dead_letter.delete_dead_letter_job("abc123-def456")

# Get statistics
stats = await soniq.dead_letter.get_dead_letter_stats(hours=24)
print(f"Total: {stats.total_count}")
print(f"By reason: {stats.by_reason}")
print(f"Oldest job: {stats.oldest_job_age_hours:.1f} hours ago")
```

### Filtered queries

```python
f = DeadLetterFilter(
    job_names=["emails.send_welcome_email"],
    reasons=["max_retries_exceeded"],
    limit=50,
)

jobs = await soniq.dead_letter.list_dead_letter_jobs(f)
```

### Bulk operations

```python
f = DeadLetterFilter(job_names=["inventory.sync_inventory"])

new_job_ids = await soniq.dead_letter.bulk_replay(
    f, reset_attempts=True, new_max_attempts=5
)
print(f"Replayed {len(new_job_ids)} jobs")
```

## Debugging a failed job

When a job lands in the DLQ, follow these steps:

1. **Find it.** List dead-letter jobs, filtered by job name or by date with `DeadLetterFilter`.

2. **Read the error.** The `last_error` field contains the exception message from the final attempt.

    ```python
    job = await soniq.dead_letter.get_dead_letter_job(job_id)
    print(job.last_error)     # "ConnectionRefusedError: ..."
    print(job.attempts)       # 4 (tried 4 times)
    print(job.args)           # {"user_id": 42}
    ```

3. **Fix the root cause.** Deploy a code fix, restore a downstream service or correct the input data.

4. **Replay.** Create a new job from the dead-letter entry. Soniq keeps the original arguments, and the DLQ row stays as the audit trail.

    ```python
    new_id = await soniq.dead_letter.replay(job_id)
    ```

5. **Verify.** Check that the replayed job completes.

    ```python
    status = await soniq.get_job(new_id)
    ```

> **Tip:** Monitor `soniq.dead_letter.get_dead_letter_stats()` and alert when jobs build up in the DLQ. A growing DLQ usually means a systemic problem: a down service, a bad deploy or bad data.

## Next steps

- [Dead-letter CLI flags](../cli/commands.md#dead-letter)
- [Dead-letter Python API](../api/dead-letter.md)
- [Dashboard](../dashboard/overview.md) to replay failed jobs in write mode
