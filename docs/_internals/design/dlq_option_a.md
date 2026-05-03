# DLQ Option A: design notes

This is the engineering design doc behind the user-facing [`../contracts/dead_letter.md`](../contracts/dead_letter.md). The contract states **what** the DLQ semantics are; this doc covers the **how** and the **why**, plus the failure-mode catalogue that operators and reviewers need.

## 1. State transition diagram

Every legal transition for a job, with the SQL operation that effects it. Disallowed transitions are listed below the diagram and are enforced by CHECK constraints (postgres), triggers (sqlite), and Python assertions (memory).

```
soniq_jobs(queued)        --claim-->     soniq_jobs(processing)
soniq_jobs(processing)    --done-->      soniq_jobs(done)
soniq_jobs(processing)    --retry-->     soniq_jobs(queued)         [attempts++]
soniq_jobs(processing)    --cancel-->    soniq_jobs(cancelled)
soniq_jobs(processing)    --DLQ-->       soniq_dead_letter_jobs     [INSERT+DELETE in one tx; row removed from soniq_jobs]
soniq_dead_letter_jobs    --replay-->    soniq_jobs(queued, fresh id, attempts=0)   [DLQ row preserved; resurrection_count++]
soniq_dead_letter_jobs    --purge-->     (deleted)
```

### Disallowed transitions (rejected by the schema)

- `done -> queued` - terminal states do not regress. Replay creates a **new** row; it does not move the original.
- `cancelled -> processing` - cancellation is final.
- `done -> processing`, `cancelled -> queued`, `cancelled -> done` - same reasoning.
- `* -> dead_letter` (on `soniq_jobs`) - the value is **not** in the enum. Writing it is rejected:
  - **Postgres**: CHECK constraint on `soniq_jobs.status` excludes `dead_letter`.
  - **SQLite**: a `BEFORE INSERT OR UPDATE` trigger on `soniq_jobs` raises when `NEW.status = 'dead_letter'`.
  - **Memory**: the in-Python write paths (insert, transition update) raise `ValueError` if the value is `'dead_letter'`.
- `soniq_dead_letter_jobs(*) -> soniq_jobs(processing)` - replay always lands as `queued` with `attempts=0`. There is no "skip the queue and start running" path.

The `soniq_jobs.status` allowed set is exactly: `queued`, `processing`, `done`, `cancelled`. Four values.

## 2. Backend asymmetry

- **Postgres**: the CHECK constraint on `soniq_jobs.status` enforces the rejection.
- **SQLite**: a `BEFORE INSERT OR UPDATE` trigger plays the same role.
- **Memory**: the Python write paths raise `ValueError` directly.

This asymmetry is the reason Group B (postgres-only schema tests) exists alongside Group A (cross-backend API parity).

## 3. Move transaction failure modes

The runtime move (`mark_job_dead_letter`) is `BEGIN; INSERT INTO soniq_dead_letter_jobs ... SELECT FROM soniq_jobs WHERE id = $1 FOR UPDATE; DELETE FROM soniq_jobs WHERE id = $1; COMMIT`. It runs inside one transaction; the row exists in exactly one table at any consistent read.

| Failure scenario | Outcome | Recovery |
| --- | --- | --- |
| Connection dies between INSERT and DELETE (postgres) | Postgres rolls back the open transaction. No DLQ row, no missing source row. | The job remains in `processing` on the original `soniq_jobs` row. Stale-worker recovery requeues it after the heartbeat-stale window; the runtime path runs again on a fresh worker; eventually the move commits. |
| Worker process crashes mid-call | Same as connection-dies (the postgres connection is reset; the open tx is rolled back). | Same: stale-recovery requeue, retry on next worker. |
| INSERT succeeds, DELETE deadlocks | Transaction rolls back (both statements undone). | Same as above; deadlock is observed by the worker as a transient failure; retried by stale-recovery. |
| INSERT fails (duplicate primary key, e.g. replay collision) | Transaction rolls back. The DLQ row's primary key equals the source `soniq_jobs.id`; collisions can only happen if the same id was already moved (would indicate a logic bug in the worker, not a normal path). | Logged as an error; the source row remains; manual operator inspection. The integration test `test_dlq_runtime_move_atomicity` asserts no duplicate state. |
| DLQ table "full" | Not a real failure mode in postgres or sqlite; there is no row-count cap. Disk full is a separate concern (the DB raises out-of-disk; same recovery as any other DB outage). | Operators size the DLQ for their failure-rate budget. |
| Memory backend mid-call exception | The in-memory move is wrapped in a try/except that restores the prior state on any exception. | Same observable contract: no duplicate, no loss. |
| SQLite SAVEPOINT rollback | sqlite uses an explicit transaction; on the injected fault, SAVEPOINT rolls back to the pre-INSERT state. | Same observable contract. |

The integration test `test_dlq_mid_transaction_crash` (Group A; runs on all three backends) injects a fault between INSERT and DELETE on each backend (postgres: connection drop; sqlite: SAVEPOINT rollback; memory: monkey-patched failure) and asserts: no duplicate state, no loss. Eventual-consistency via stale-recovery is acceptable; the contract is "exactly one of: pre-move state, post-move state" at any consistent read.

## 4. Why Option A

The `soniq_dead_letter_jobs` schema carries all the DLQ-row columns we need (`dead_letter_reason`, `tags`, `resurrection_count`, `last_resurrection_at`, `original_created_at`, `moved_to_dead_letter_at`). Option A keeps the runtime move as a single atomic transaction and removes terminal-state churn from the hot `soniq_jobs` table.

## 5. Cross-references

- User-facing contract: [`../contracts/dead_letter.md`](../contracts/dead_letter.md).
- Stats contract that consumes the DLQ table: [`../contracts/queue_stats.md`](../contracts/queue_stats.md).
