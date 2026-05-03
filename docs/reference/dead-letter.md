# Dead Letter Queue

The dead-letter queue (DLQ) captures jobs that have exhausted all retries. They land in a separate `soniq_dead_letter_jobs` table where you can inspect, debug, and replay them.

## Setup

The DLQ table is part of the core schema. Run `soniq setup` once to create
the schema; after that, any job that fails after its final retry attempt
is moved into `soniq_dead_letter_jobs` automatically.

## How jobs get there

A job enters the DLQ when:

- It has used all retry attempts and still fails (`max_retries_exceeded`)
- It raises a permanent/unrecoverable error (`permanent_failure`)
- The job function is no longer registered (`job_not_found`)
- Arguments fail validation (`invalid_arguments`)
- It exceeds its timeout after all retries (`timeout`)

Each dead-letter record preserves the original job name, arguments, queue, priority, error message, and attempt count.

## CLI management

### List dead-letter jobs

```bash
soniq dead-letter list
soniq dead-letter list --limit 20
soniq dead-letter list --filter "send_welcome_email"
```

### Replay a job

Replay creates a new `soniq_jobs` row with the same function and arguments,
reset to `queued` status. The original DLQ row stays as the audit trail and
its `resurrection_count` is incremented:

```bash
soniq dead-letter replay abc123-def456
```

### Delete a dead-letter job

```bash
soniq dead-letter delete abc123-def456
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
`app.dead_letter.<method>()`. The handle is constructed lazily on first
access and cached on the instance.

```python
from soniq import Soniq
from soniq.features.dead_letter import DeadLetterFilter

app = Soniq(database_url="postgresql://localhost/myapp")

# List all dead-letter jobs
jobs = await app.dead_letter.list_dead_letter_jobs()

# Get a specific job
job = await app.dead_letter.get_dead_letter_job("abc123-def456")

# Replay with options
new_job_id = await app.dead_letter.replay(
    "abc123-def456",
    reset_attempts=True,       # start fresh (default)
    new_max_attempts=10,       # give it more tries this time
    new_queue="retry-queue",   # route to a different queue
)

# Delete permanently
await app.dead_letter.delete_dead_letter_job("abc123-def456")

# Get statistics
stats = await app.dead_letter.get_dead_letter_stats(hours=24)
print(f"Total: {stats.total_count}")
print(f"By reason: {stats.by_reason}")
print(f"Oldest job: {stats.oldest_job_age_hours:.1f} hours ago")
```

### Filtered queries

```python
f = DeadLetterFilter()
f.job_names = ["myapp.tasks.send_welcome_email"]
f.reasons = ["max_retries_exceeded"]
f.limit = 50

jobs = await app.dead_letter.list_dead_letter_jobs(f)
```

### Bulk operations

```python
f = DeadLetterFilter()
f.job_names = ["myapp.tasks.sync_inventory"]

new_job_ids = await app.dead_letter.bulk_replay(
    f, reset_attempts=True, new_max_attempts=5
)
print(f"Replayed {len(new_job_ids)} jobs")
```

## Debugging a failed job

When a job lands in the DLQ, follow this workflow:

1. **Find it.** List dead-letter jobs filtered by job name or time range.

2. **Read the error.** The `last_error` field contains the exception message from the final attempt.

    ```python
    job = await app.dead_letter.get_dead_letter_job(job_id)
    print(job.last_error)     # "ConnectionRefusedError: ..."
    print(job.attempts)       # 4 (tried 4 times)
    print(job.args)           # {"user_id": 42}
    ```

3. **Fix the root cause.** Deploy a code fix, restore a downstream service, or correct the input data.

4. **Replay.** Create a new job from the dead-letter entry. The original arguments are preserved; the DLQ row stays as the audit trail.

    ```python
    new_id = await app.dead_letter.replay(job_id)
    ```

5. **Verify.** Check that the replayed job completes successfully.

    ```python
    status = await app.get_job(new_id)
    ```

> **Tip:** Set up monitoring on `app.dead_letter.get_dead_letter_stats()` to alert when jobs accumulate in the DLQ. A growing DLQ usually signals a systemic issue -- a down service, a bad deploy, or a data problem.
