# Shutdown contract

This document is the source of truth for worker shutdown behavior. The state diagram below is canonical; implementation, tests, and user-facing prose all conform to it.

## Boundedness contract

Shutdown is bounded for async handlers and **not** bounded for sync handlers.

- **Async in-flight:** total shutdown wall time is bounded by `shutdown_timeout` (default 30s). On timeout the asyncio task is cancelled and the row is NACK'd via `nack_job`.
- **Sync in-flight:** the wait has three distinct windows, additive rather than min'd. (1) During DRAINING, the sync thread is waited on along with the rest of in-flight work, bounded by the remaining `shutdown_timeout`. (2) When `shutdown_timeout` expires (FORCE_TIMEOUT_PATH on the sync branch), an **additional grace budget of `sync_handler_grace_seconds`** starts from that instant - flat, not clamped by `remaining_shutdown_timeout` (which is zero by definition). (3) If the grace expires too, the worker stops fetching but **the process cannot be force-exited from inside Python while the thread is alive.** It therefore runs until either (a) the thread returns on its own, or (b) the orchestrator/supervisor sends SIGKILL. Case (b) is the documented hard cut-off. Total shutdown wall time for sync handlers is **unbounded by Soniq alone**; the operator's supervisor deadline (k8s `terminationGracePeriodSeconds`, systemd `TimeoutStopSec`) is the only bound. SIGKILL implies duplicate-execution risk on the next stale-recovery run; this is the documented contract for non-idempotent sync work.

The post-timeout state is named **`FORCE_TIMEOUT_PATH`** (renamed from `FORCE_REQUEUE` in v8.5+) because only the async branch actually requeues; the sync branch hands off to wait-for-thread plus stale-recovery.

## State diagram

```
RUNNING --SIGTERM--> DRAINING --all jobs done--> STOPPED
                       |
                       +--shutdown_timeout elapsed--> FORCE_TIMEOUT_PATH
                                                          |
                                                          +-- async branch ---> nack_job (row -> queued, attempts preserved) --> STOPPED
                                                          |
                                                          +-- sync branch  ---> WAIT_FOR_THREAD (extra budget = sync_handler_grace_seconds)
                                                                                    |
                                                                                    +-- thread returns within grace --> mark done/failed --> STOPPED
                                                                                    |
                                                                                    +-- grace expires --> keep waiting for thread (no Soniq-side force-exit)
                                                                                            |
                                                                                            +-- thread returns --> mark done/failed --> STOPPED
                                                                                            |
                                                                                            +-- supervisor SIGKILL --> process killed mid-handler;
                                                                                                                        row stays `processing`;
                                                                                                                        stale-worker recovery requeues in
                                                                                                                        a *subsequent* worker process
                                                                                                                        (handler may run twice; documented contract)
```

The diagram is the source of truth. No path in the sync branch goes through `nack_job`; Soniq does not requeue sync handlers on its own. Only stale-worker recovery in a *subsequent* worker process puts the row back to `queued`, and that path runs after the original process is dead.

## States

- **RUNNING:** worker fetches and executes normally.
- **DRAINING:** worker stops fetching new jobs; awaits in-flight handler tasks. Heartbeat continues.
- **FORCE_TIMEOUT_PATH:** behavior diverges by handler type because threads cannot be reliably cancelled.
  - **Async handlers:** the asyncio task is cancelled; the corresponding `soniq_jobs` row is NACK'd back to `queued` via `nack_job` (see field contract below). The reason (`shutdown_timeout`) is emitted via the worker log and the metrics sink. **No schema column added** for the reason.
  - **Sync handlers:** the underlying thread cannot be cancelled. The worker awaits the sync thread for an additional flat budget of `sync_handler_grace_seconds` (default `= job_timeout`), measured from the moment FORCE_TIMEOUT_PATH triggers. **This is not `min(remaining_shutdown_timeout, sync_handler_grace_seconds)`** - that formula collapses to zero at `shutdown_timeout` expiry, which is exactly when this window starts.
- **WAIT_FOR_THREAD** (sync branch only): after the grace budget expires, the worker keeps waiting for the in-flight thread. It does **not** call `sys.exit` or otherwise force the process down (Python won't actually exit while non-daemon threads are alive, and racing the thread to mutate the row was the v6 duplicate-execution bug). Heartbeats continue.
  - **Thread returns:** the worker observes completion, marks the row `done` or `failed` per the result, and exits cleanly.
  - **Supervisor SIGKILL:** the operator's process supervisor (k8s `terminationGracePeriodSeconds`, systemd `TimeoutStopSec`, supervisor) is the only authority that can terminate a stuck sync handler. When it sends SIGKILL, the process dies mid-execution; the row stays `processing`; stale-worker recovery requeues it after the heartbeat-stale window; **the handler will run again, possibly producing duplicate side-effects.**
- **STOPPED:** process exits (after all in-flight work has either completed or been NACK'd; sync threads have all returned naturally; supervisor SIGKILL is the only other exit path).

## Per-handler-type precedence rules (locked)

Both rules use the same effective deadline calculation - `min(job_timeout, time_remaining_in_shutdown_timeout)` - but the consequences of firing differ.

- **Async handlers (timeouts are control-plane AND execution-plane):** `job_timeout` and `shutdown_timeout` are independent. A job's effective deadline is `min(job_timeout, time_remaining_in_shutdown_timeout)` once shutdown begins. Whichever fires first wins because cooperative cancellation actually stops execution. A job that hits `job_timeout` during DRAINING follows the normal timeout failure path (counts an attempt, retried per policy). A job that hits FORCE_TIMEOUT_PATH (i.e. `shutdown_timeout` fires first) is NACK'd via `nack_job` with `attempts` **preserved** (not incremented). Rationale: the worker abandoned the job, the job did not fail.
- **Sync handlers (timeouts govern control-plane decisions only; execution may continue):** the same `min(job_timeout, time_remaining_in_shutdown_timeout)` calculation runs, but **firing a deadline does not stop the thread**. The deadline only governs what the worker writes to the row and what the worker process does next:
  - `job_timeout` fires: the await path raises, the row is marked timed-out per the normal failure policy (counts an attempt). The thread keeps running in the background; the `sync_handler_pool_size` semaphore permit stays held by that thread until it returns. Side effects after this point are out of Soniq's control; sync handlers must be idempotent.
  - `shutdown_timeout` fires (FORCE_TIMEOUT_PATH on the sync branch): the worker stops fetching and starts the additional `sync_handler_grace_seconds` budget; if it expires, `WAIT_FOR_THREAD` continues unbounded. Soniq does **not** call `nack_job` for sync handlers - the row stays `processing`. Either the thread returns (worker writes done/failed and exits cleanly) or supervisor SIGKILL ends the process (row stays `processing`; stale-recovery requeues with documented duplicate-execution risk).

`shutdown_timeout` defaults to 30s, configurable per-instance. `sync_handler_grace_seconds` defaults to `job_timeout`, configurable per-instance.

## `nack_job` field contract (locked, identical across postgres/sqlite/memory)

```sql
UPDATE soniq_jobs
SET status = 'queued',
    worker_id = NULL,
    updated_at = NOW(),
    scheduled_at = NOW()
WHERE id = $1 AND status = 'processing'
```

- `last_error` is **not** modified (the job was abandoned, not failed).
- `attempts` is **not** modified (preserved).
- The WHERE clause makes the operation idempotent: if the row was already advanced by stale-worker recovery, the UPDATE is a no-op.
- Memory backend mirrors the same field set in its in-Python state; sqlite uses the same SQL with `CURRENT_TIMESTAMP` in place of `NOW()`.
- The shutdown reason is emitted via `logger.warning` and `metrics_sink.emit("shutdown_nack", {...})`, **not persisted on the row**.

`nack_job` is invoked **only on the async FORCE_TIMEOUT_PATH branch**. The sync branch never calls `nack_job`; the row stays `processing` and is reclaimed by stale-worker recovery in a subsequent process.

## Sync `job_timeout` best-effort warning

`job_timeout` against a sync handler is **best-effort**. The await path uses `asyncio.wait_for` over the executor `Future`; when it expires, `asyncio.TimeoutError` is raised in the worker's await path and the row is marked timed-out per the normal failure policy. **The underlying thread continues to run until the handler returns.** Python provides no safe way to interrupt a thread mid-execution. Side effects after the timeout are out of Soniq's control.

Operators must:

- Write idempotent sync handlers (the thread may keep running after a timeout has been recorded; a stale-recovery replay or a retry may run the handler again on top of partial side effects).
- Treat `sync_handler_grace_seconds` as the worst-case shutdown extension for non-idempotent paths; set it longer than worst-case sync runtime to avoid the SIGKILL-replay scenario.
- Accept that hard-kill (orchestrator SIGKILL after its own deadline) replays the handler. This is documented at-least-once delivery with explicit duplicate-execution risk for sync handlers.

Async handlers do not carry this risk because cooperative cancellation actually stops execution before `nack_job` runs.

## Bounded claimed-not-yet-running window

Because saturation is enforced by post-claim semaphore (job type is unknown until after claim), a sync job can sit briefly in `processing` before its handler thread starts. The window is bounded:

- At most `worker_concurrency` (existing setting) sync jobs may be in `processing` while waiting for a `sync_handler_pool_size` permit.
- The window is brief in practice; it surfaces in `processing` count.
- During shutdown, these claimed-not-yet-running rows count toward the same DRAINING/FORCE_TIMEOUT_PATH budget. If the worker is shutting down before the permit was acquired, the wrapping task is on the loop and is async-cancelable; treat it as the async branch.

## Cross-references

- `processing` count semantics in queue stats (includes claimed-but-not-yet-running): [`queue_stats.md`](queue_stats.md).
- DLQ behavior is unrelated to shutdown; a job mid-shutdown does not become a DLQ row. See [`dead_letter.md`](dead_letter.md).
