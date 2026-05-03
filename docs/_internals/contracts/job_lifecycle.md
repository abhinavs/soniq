# Job lifecycle contract

`soniq_jobs.status` carries exactly four live values. Every other
former state has been retired or moved to a different table.

## Live row states

```
queued -> processing -> {done, cancelled}
```

| Value | Terminal? | Meaning |
| --- | --- | --- |
| `queued` | no | runnable now or scheduled for the future |
| `processing` | no | claimed by a worker (includes the brief post-claim semaphore wait for sync handlers) |
| `done` | yes | handler returned successfully |
| `cancelled` | yes | explicit cancellation via the API or CLI |

Anything else is rejected at the storage layer:

- **Postgres**: a CHECK constraint pins the four values.
- **SQLite**: a `BEFORE INSERT OR UPDATE` trigger raises on any other value.
- **Memory**: the in-memory write paths raise `ValueError`.

## What the lifecycle is *not*

- There is no `failed` row state. A handler that raises either
  re-queues for retry (`status` flips back to `queued` with
  `attempts` incremented) or moves to `soniq_dead_letter_jobs` if the
  retry budget is exhausted. **Nothing produces `status='failed'` in
  steady state.**
- There is no `dead_letter` row state. DLQ rows live in their own
  table; see [`dead_letter.md`](dead_letter.md).
- There is no `failed -> queued` transition. The retry path goes
  `processing -> queued` directly. The pre-0.0.2 backend method
  `retry_job(job_id)` that gated on `status='failed'` is gone.

## Replay (post-DLQ)

Replay is the only path that re-introduces a job after a terminal
failure. It does **not** mutate the failed row or transition status:

- Inserts a **new** `soniq_jobs` row with a fresh id, `status='queued'`,
  `attempts=0`. Retry policy applies normally to the new row.
- Updates the DLQ row in the **same transaction**:
  `resurrection_count = resurrection_count + 1`,
  `last_resurrection_at = NOW()`. The DLQ row is preserved as the
  audit trail.
- Operators can replay the same DLQ row multiple times; each call
  produces a distinct new `soniq_jobs.id`.

See [`dead_letter.md`](dead_letter.md) for the full DLQ contract.

The four-state CHECK constraint is set on the column when
`0001_core.sql` runs. There is no `failed` or `dead_letter` row
state in `soniq_jobs` at any point in 0.0.2.
