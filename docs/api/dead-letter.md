# Dead-letter queue

The `DeadLetterService` handles jobs that exhausted every retry. It is reached
through your `Soniq` instance as `app.dead_letter` - the handle is built lazily
on first access and cached on the instance, so it always talks to that
instance's database.

For the table schema, the CLI, and a debugging walkthrough see
[Dead-letter queue](../reference/dead-letter.md). This page is the Python API.

```python
from soniq import Soniq
from soniq.features.dead_letter import DeadLetterFilter

app = Soniq(database_url="postgresql://localhost/myapp")

jobs = await app.dead_letter.list_dead_letter_jobs(
    DeadLetterFilter(job_names=["billing.charge"], limit=50)
)
for job in jobs:
    print(job.id, job.last_error)
```


## DeadLetterFilter

Query filter for the list/bulk methods. A `@dataclass` - construct it with
keyword arguments (or no arguments and assign attributes afterward). All fields
are optional.

| Field | Type | Default | Description |
|---|---|---|---|
| `job_names` | `list[str] \| None` | `None` | Match any of these task names. |
| `queues` | `list[str] \| None` | `None` | Match any of these queues. |
| `reasons` | `list[str] \| None` | `None` | Match any of these dead-letter reasons (see `DeadLetterReason`). |
| `date_from` | `datetime \| None` | `None` | Only jobs moved to the DLQ at or after this time. |
| `date_to` | `datetime \| None` | `None` | Only jobs moved to the DLQ at or before this time. |
| `tags` | `dict[str, str] \| None` | `None` | Match rows whose `tags` JSON contains every given key/value. |
| `has_been_resurrected` | `bool \| None` | `None` | `True` = replayed at least once, `False` = never replayed, `None` = either. |
| `limit` | `int` | `1000` | Maximum rows to return. |
| `offset` | `int` | `0` | Rows to skip (pagination). |

```python
f = DeadLetterFilter(
    job_names=["myapp.tasks.sync_inventory"],
    reasons=["max_retries_exceeded"],
    limit=100,
)
```


## DeadLetterJob

The record returned by `list_dead_letter_jobs()` and `get_dead_letter_job()`. A
frozen-shaped `@dataclass`.

| Attribute | Type | Description |
|---|---|---|
| `id` | `str` | UUID of the dead-letter row. Pass this to `replay()` / `delete_dead_letter_job()`. |
| `job_name` | `str` | Task name of the failed job. |
| `args` | `dict` | The original job arguments. |
| `queue` | `str` | Queue the job ran in. |
| `priority` | `int` | Original priority. |
| `max_attempts` | `int` | Attempt cap the job had. |
| `attempts` | `int` | How many times it actually ran. |
| `last_error` | `str` | Exception message from the final attempt. |
| `dead_letter_reason` | `str` | Why it landed here (`max_retries_exceeded`, `permanent_failure`, `job_not_found`, `invalid_arguments`, `timeout`, `resource_exhausted`, `manual_move`). |
| `original_created_at` | `datetime` | When the job was first enqueued. |
| `moved_to_dead_letter_at` | `datetime` | When it entered the DLQ. |
| `resurrection_count` | `int` | Times it has been replayed (starts at 0). |
| `last_resurrection_at` | `datetime \| None` | When it was last replayed. |
| `tags` | `dict[str, str] \| None` | Free-form tags added via `add_tags_to_job()`. |


## Methods

All methods are `async`.

### list_dead_letter_jobs

```python
async def list_dead_letter_jobs(
    filter_criteria: DeadLetterFilter | None = None,
) -> list[DeadLetterJob]
```

Return dead-letter rows, newest first. With no filter, returns up to the
`DeadLetterFilter` default of 1000 rows.

### get_dead_letter_job

```python
async def get_dead_letter_job(dead_letter_id: str) -> DeadLetterJob | None
```

Fetch a single row by id. `None` if it does not exist.

### replay

```python
async def replay(
    dead_letter_id: str,
    reset_attempts: bool = True,
    new_max_attempts: int | None = None,
    new_priority: int | None = None,
    new_queue: str | None = None,
) -> str | None
```

Mint a fresh `soniq_jobs` row from the dead-letter row and, in the same
transaction, increment `resurrection_count` on the DLQ row. The DLQ row is kept
as the audit trail, so the same row can be replayed more than once. Returns the
new job's UUID, or `None` if the id is unknown **or the target job is not
registered on this `Soniq` instance** - the registry lookup is how `replay`
knows the new row's attempt cap, so the job's module must be imported first
(this is why `soniq dead-letter replay` needs `--jobs-modules`).

| Argument | Default | Effect |
|---|---|---|
| `reset_attempts` | `True` | Start the new job at `attempts=0`. `False` carries the old count over. |
| `new_max_attempts` | `None` | Override the attempt cap for the replayed job. |
| `new_priority` | `None` | Override the priority. |
| `new_queue` | `None` | Route the replayed job to a different queue. |

```python
new_id = await app.dead_letter.replay(
    job.id, new_max_attempts=10, new_queue="retry-slow"
)
```

### bulk_replay

```python
async def bulk_replay(
    filter_criteria: DeadLetterFilter,
    reset_attempts: bool = True,
    new_max_attempts: int | None = None,
) -> list[str]
```

Replay every row matching the filter. Rows whose job is unregistered are skipped
(logged, not raised). Returns the list of new job UUIDs.

### delete_dead_letter_job

```python
async def delete_dead_letter_job(dead_letter_id: str) -> bool
```

Permanently remove one dead-letter row. `True` if a row was deleted.

### bulk_delete

```python
async def bulk_delete(filter_criteria: DeadLetterFilter) -> int
```

Permanently remove every row matching the filter. Returns the count deleted. An
empty filter deletes the whole table, so pass a real filter.

### cleanup_old_dead_letter_jobs

```python
async def cleanup_old_dead_letter_jobs(days: int = 30) -> int
```

Delete rows older than `days` (by `moved_to_dead_letter_at`). Returns the count
deleted. Good for a `@app.periodic` housekeeping job.

### get_dead_letter_stats

```python
async def get_dead_letter_stats(hours: int | None = None) -> DeadLetterStats
```

Aggregate counts over the whole table, or the last `hours` if given.
`DeadLetterStats` has: `total_count`, `by_job_name`, `by_queue`, `by_reason`,
`by_date` (all `dict[str, int]`), `oldest_job_age_hours` (`float`), and
`resurrection_success_rate` (`float`). Point your DLQ alerting at this.

### add_tags_to_job

```python
async def add_tags_to_job(dead_letter_id: str, tags: dict[str, str]) -> bool
```

Merge `tags` into the row's existing tags (JSON). `True` if the row was updated.
Filter on them later with `DeadLetterFilter(tags={...})`.

### export_dead_letter_jobs

```python
async def export_dead_letter_jobs(
    filter_criteria: DeadLetterFilter | None = None,
    format: str = "json",
) -> str
```

Write matching rows to a timestamped file (`"json"` or `"csv"`) in the current
directory and return the filename.
