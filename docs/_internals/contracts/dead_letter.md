# Dead-letter contract (Option A)

`soniq_dead_letter_jobs` is the **single source of truth** for dead-lettered jobs. The `dead_letter` value is **not** present in the `soniq_jobs.status` enum; backends reject any write that tries to set it.

## Lifecycle

A job that exhausts retries follows this path; nothing else creates a DLQ row.

```
soniq_jobs(processing) --DLQ--> soniq_dead_letter_jobs   [INSERT + DELETE in one tx]
soniq_dead_letter_jobs --replay--> soniq_jobs(queued, fresh id, attempts=0)
                                                          [DLQ row preserved; resurrection_count++]
soniq_dead_letter_jobs --purge--> (deleted)
```

- **Runtime move:** when the worker decides a job has exhausted retries, `mark_job_dead_letter` runs **`INSERT INTO soniq_dead_letter_jobs ... SELECT FROM soniq_jobs WHERE id = $1` followed by `DELETE FROM soniq_jobs WHERE id = $1`, both inside one transaction.** The DLQ row's primary key equals the original `soniq_jobs.id` (no id remapping; audit trails and external references stay stable). On commit, the row exists in exactly one table.
- **Replay** (`DeadLetterService.replay`): inserts a **new** `soniq_jobs` row with a fresh UUID, `status='queued'`, `attempts=0`, args/queue/priority copied from the DLQ row. The DLQ row is **not** deleted; it is updated in the same transaction with `resurrection_count = resurrection_count + 1` and `last_resurrection_at = NOW()`. Operators can replay the same DLQ row multiple times; each call yields a distinct new `soniq_jobs` row.
- **Purge** (`DeadLetterService.purge`): deletes the row from `soniq_dead_letter_jobs`. There is no soft-delete; purge is final.

## Status enum

`soniq_jobs.status` allowed values:

| Value | Terminal? | Meaning |
| --- | --- | --- |
| `queued` | no | runnable or scheduled-future |
| `processing` | no | claimed by a worker (includes brief post-claim semaphore wait for sync jobs) |
| `done` | yes | success |
| `cancelled` | yes | explicit cancellation |

`dead_letter` is **not** in this enum. Backends reject writes via:

- **Postgres**: CHECK constraint on `soniq_jobs.status` that excludes `dead_letter`.
- **SQLite**: a `BEFORE INSERT OR UPDATE` trigger on `soniq_jobs` that raises when `NEW.status = 'dead_letter'`. (SQLite has no ENUM; the trigger is the rejection mechanism.)
- **Memory**: a Python `ValueError` raised by the in-memory write paths (insert and transition update).

The mechanism varies; the observable contract is identical: writing `dead_letter` to `soniq_jobs.status` is **rejected**. The integration test `test_dlq_status_rejects_dead_letter` (Group A; runs on all three backends) asserts the rejection regardless of mechanism.

## Public API

`DeadLetterService` exposes `list`, `replay`, and `purge`. There is no `move(job_id)` helper: the runtime is the only path that creates DLQ rows. A manual move from outside the worker would either duplicate the runtime path or create rows that bypassed retry policy entirely.

## Cross-table consistency

- `get_queue_stats()['dead_letter']` is `COUNT(*)` on `soniq_dead_letter_jobs`. There is no double-counting because the runtime move is a single transaction; a row is in exactly one table at any consistent read.
- A `get_queue_stats` query that races a runtime move sees one of two consistent states (pre-move: row in `soniq_jobs`; post-move: row in DLQ). It never sees both, never sees neither.

## Replay invariants

- Replay does **not** reset the original `soniq_jobs` row - that row no longer exists. The DLQ row stays.
- Replay produces a **new** `soniq_jobs.id`. The lineage to the DLQ row is not stored as a column; the DLQ row's `resurrection_count` is the operator-facing audit signal.
- `attempts=0` on the new row means the replayed job gets the full retry budget again. Retry policy applies normally to the new row.

## Purge invariants

- Purge is final. Once gone, the row cannot be recovered from within Soniq; restore from operator backup if needed.
- Purge does not touch `soniq_jobs`. There is no `soniq_jobs` row to clean up; the runtime move already deleted it.

## Operator notes

- DLQ growth is unbounded by Soniq itself. Operators should size the DLQ table for their failure-rate budget.
- Replays are not idempotent across calls (each call creates a new row); they are atomic per call. Tooling that wraps replay must dedupe upstream if needed.

## Cross-references

- DLQ design notes: [`../design/dlq_option_a.md`](../design/dlq_option_a.md).
- `dead_letter` as a queue-stats key (cross-table): [`queue_stats.md`](queue_stats.md).
