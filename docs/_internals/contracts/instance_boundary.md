# Instance boundary contract

A `Soniq` instance is the unit of ownership for all settings, registries, backends, and worker resources. Two `Soniq` instances in one process must not bleed state into each other - including settings, registry entries, backend connections, and metrics state. This contract defines what an instance owns, what is allowed to remain process-global, and the lint rule that enforces the boundary.

## What a `Soniq` instance owns

Each instance carries the following, scoped to the instance and its constructor arguments:

- **Settings.** `Soniq(settings=...)` resolves once at construction. There is no global `get_settings()` lookup at runtime in core paths.
- **Registries.** Job registry, scheduler registry, dead-letter service handle, plugin contracts. All bound to the instance and reachable via the instance only.
- **Backend.** The `Backend` (postgres / sqlite / memory) is owned by the instance. Connection pools and prepared-statement caches are per-instance.
- **Worker resources.** Per-instance `asyncio.Semaphore(sync_handler_pool_size)`, per-instance `ThreadPoolExecutor(max_workers=sync_handler_pool_size)`, per-instance heartbeat task, per-instance shutdown state machine.
- **Metrics state.** `_job_metrics_index` and any rolling counters live on the instance. The metrics CLI resolves the instance via the existing `--database-url` / env path, not via a global app fallback.
- **CLI scope.** All CLI commands resolve their instance from explicit flags / env (`--database-url`, etc.) - no implicit "ambient instance" lookup.

The two-instance bleed test (`tests/integration/test_two_instance_bleed.py`) constructs two `Soniq` instances with different settings (different `job_timeout`, different `result_ttl`, different queue prefixes) in one process, runs jobs against both, and asserts no bleed in any direction.

## Allowed process-global state (locked, exhaustive)

The only state allowed to be process-global in 0.0.2 is **logging configuration**. Specifically:

- The Python `logging` module's root logger and handler chain. `Soniq` may install its own log formatter once, on first construction, but does not own the logging stack and does not assume exclusivity.
- That is the **entire** allowed-globals list. There is nothing else.

In particular, the following are **not** allowed to be globals:

- Settings (`get_settings()` at runtime).
- Registries.
- Backend handles or connection pools.
- The "active" `Soniq` instance via a module-level variable or a `ContextVar` consulted from core paths.
- Metrics state.

If a future feature genuinely needs new global state, this contract must be amended first; no PR may quietly add a global.

## Lint rule (summary)

`scripts/check_no_global_settings.py` is the enforcement mechanism. The rule:

- **Greps for `get_settings()` calls.** Any call outside the **constructor / bootstrap allowlist** is a CI failure.
- **Allowlist (locked):** the explicit construction paths (`Soniq.__init__`, the CLI bootstrap that builds an instance from flags, the test fixtures that build an instance for tests). Module-level imports and runtime helpers are **not** on the allowlist.
- **Output:** a single CI step that exits non-zero with the offending file:line on violation. Release gate 9 ("`scripts/check_no_global_settings.py` passes in CI") depends on it.

Why a grep, not an AST tool: `get_settings()` is a single name with a small, known import path. A grep with the allowlist is small enough to read in one screen and unambiguous in its semantics. An AST tool here would obscure intent.

## What this contract is **not**

- Not a per-thread isolation contract. Threads inside a single instance share that instance's state freely. Bleed only matters across instances.
- Not a multi-process contract. Two worker processes sharing a database backend are independent OS processes; the instance boundary is about same-process bleed.

## Cross-references

- `scripts/check_no_global_settings.py`: the enforcement script.
- `tests/integration/test_two_instance_bleed.py`: the verification test.
