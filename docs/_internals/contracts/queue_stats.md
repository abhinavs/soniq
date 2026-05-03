# Queue stats contract

`Soniq.get_queue_stats()` (and the `soniq status` CLI that consumes it) returns a `QueueStats` mapping with **exactly** the six keys below. Any other key is a bug; any missing key is a bug. Backends must produce the canonical shape directly - no aliases, no extras.

## Canonical keys

```
{
    "total":      int,  # sum of the five bucket counts below
    "queued":     int,  # rows in soniq_jobs with status='queued'
    "processing": int,  # rows in soniq_jobs with status='processing'
    "done":       int,  # rows in soniq_jobs with status='done'
    "dead_letter":int,  # rows in soniq_dead_letter_jobs (cross-table; see below)
    "cancelled":  int,  # rows in soniq_jobs with status='cancelled'
}
```

- The `failed` key from older alpha versions is **gone**. Failures that exhaust retries land in the DLQ and are counted under `dead_letter`. In-flight failures that will retry remain in `queued` (after `nack_job`) or `processing`.
- `total` is defined as `queued + processing + done + dead_letter + cancelled`. It is not a separate query; backends compute it from the five bucket counts to guarantee internal consistency.
- The order above is the canonical order. Display tooling (`soniq status`) follows it.

## Semantics per key

| Key | Source | Notes |
| --- | --- | --- |
| `queued` | `soniq_jobs` where `status='queued'` | includes scheduled-future jobs (`scheduled_at > now()`); the API does not split scheduled-vs-runnable in stats. |
| `processing` | `soniq_jobs` where `status='processing'` | includes claimed-but-not-yet-running sync jobs that are awaiting a `sync_handler_pool_size` permit (see `shutdown.md` for why this window exists and is brief). |
| `done` | `soniq_jobs` where `status='done'` | terminal success state. |
| `dead_letter` | `COUNT(*) FROM soniq_dead_letter_jobs` | **cross-table**. The DLQ table is the single source of truth. The `dead_letter` value has been removed from the `soniq_jobs.status` enum; reading it back from `soniq_jobs` is impossible by construction. |
| `cancelled` | `soniq_jobs` where `status='cancelled'` | terminal explicit-cancel state. |

The `dead_letter` cross-table aggregation is the only departure from a single GROUP BY on `soniq_jobs.status`. Backends document the join (or the equivalent in-memory list traversal) explicitly.

## Backend implementation notes

All three backends ship in 0.0.2 with the canonical shape directly. No backend may return additional keys; no backend may omit a key.

- **Postgres** (`soniq/backends/postgres/__init__.py:get_queue_stats`):
  - One `SELECT status, COUNT(*) FROM soniq_jobs GROUP BY status` for the four `soniq_jobs` buckets.
  - One `SELECT COUNT(*) FROM soniq_dead_letter_jobs` for `dead_letter`.
  - Buckets that come back empty default to `0` (not omitted).
- **SQLite** (`soniq/backends/sqlite.py:get_queue_stats`):
  - Same two queries, identical semantics. The DLQ table is provisioned by the existing schema bootstrap.
- **Memory** (`soniq/testing/memory_backend.py:get_queue_stats`):
  - Iterate the in-Python jobs dict and count by `status`.
  - Read the in-memory DLQ list length for `dead_letter`.

Two-table aggregation cost is acceptable: `get_queue_stats` is not on the hot path. It is called by the `soniq status` CLI and by operator dashboards on demand, not per-job.

## Error-on-extra-keys rule

The `QueueStats` TypedDict (defined in `soniq/types.py`) is declared in **closed** form. The contract test (`tests/contract/test_queue_stats_keys.py`, parameterized over all three backends) asserts `set(stats.keys()) == {"total", "queued", "processing", "done", "dead_letter", "cancelled"}`. A backend that leaks a column name as an extra stats key fails the contract test. A backend that omits a key fails the contract test. Both failures block release gate 1.

The CLI (`soniq/cli/status.py`) consumes the canonical keys positionally; an unknown key from a backend would surface as a KeyError, but the contract test catches it before the CLI ever runs.

## What this contract is **not**

- Not a metrics snapshot. Metrics history lives in the metrics tables and the operator dashboards; queue stats is a point-in-time count.
- Not a per-queue breakdown. The current stats contract is whole-instance.
- Not a job listing. Use the explicit list APIs for that.

## Cross-references

- DLQ source-of-truth and lifecycle: [`dead_letter.md`](dead_letter.md).
- Where `processing` rows can transiently grow because of post-claim semaphore waits: [`shutdown.md`](shutdown.md), Bounded claimed-not-yet-running window.
