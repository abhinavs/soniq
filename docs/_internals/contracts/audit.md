# Doc / manifest audit

Tracked artifact. Every public command, environment variable, and setting referenced from `docs/`, `deployment/`, `README.md`, `CHANGELOG.md`, or `docs/production/` lives in the table below. The table is the single source of truth for what the doc smoke job iterates over.

Status values:

- `implemented` - the symbol exists in code at the listed `Code reference` and the doc reference is accurate.
- `removed` - the symbol is referenced from the docs but does not exist in code today. The follow-up is to delete the doc reference.

Smoke category:

- `auto-smoke` - read-only, runnable in CI against an ephemeral test instance. The doc smoke job exercises these and asserts exit code 0.
- `manual-only` - mutating, interactive, long-running, or environment-only. Documented but skipped by the smoke job.

## CLI commands

| Source (file:line)                           | Symbol (command/env/setting)    | Code reference                                                                               | Status      | Smoke category |
| -------------------------------------------- | ------------------------------- | -------------------------------------------------------------------------------------------- | ----------- | -------------- |
| docs/cli/commands.md:16                      | `soniq setup`                   | soniq/cli/setup.py:11 (`add_setup_cmd`)                                                      | implemented | manual-only    |
| docs/cli/commands.md:35                      | `soniq worker`                   | soniq/cli/worker.py (`add_worker_cmd`)                                                       | implemented | manual-only    |
| docs/cli/commands.md:61                      | `soniq status`                  | soniq/cli/status.py (`add_status_cmd`)                                                       | implemented | auto-smoke     |
| docs/cli/commands.md:86                      | `soniq inspect`                 | soniq/cli/inspect.py (`add_inspect_cmd`)                                                     | implemented | auto-smoke     |
| docs/cli/commands.md:116                     | `soniq dead-letter`             | soniq/cli/dead_letter.py:9 (`add_dead_letter_cmd`)                                           | implemented | manual-only    |
| docs/cli/commands.md:124                     | `soniq dead-letter list`        | soniq/cli/dead_letter.py:72                                                                  | implemented | auto-smoke     |
| docs/cli/commands.md:130                     | `soniq dead-letter replay`      | soniq/cli/dead_letter.py:78                                                                  | implemented | manual-only    |
| docs/cli/commands.md:137                     | `soniq dead-letter delete`      | soniq/cli/dead_letter.py (action=delete)                                                     | implemented | manual-only    |
| docs/cli/commands.md:144                     | `soniq dead-letter cleanup`     | soniq/cli/dead_letter.py (action=cleanup)                                                    | implemented | manual-only    |
| docs/cli/commands.md:151                     | `soniq dead-letter export`      | soniq/cli/dead_letter.py (action=export)                                                     | implemented | manual-only    |
| docs/cli/commands.md:175                     | `soniq dashboard`               | soniq/cli/dashboard.py (`add_dashboard_cmd`)                                                 | implemented | manual-only    |
| docs/cli/commands.md:203                     | `soniq scheduler`               | soniq/cli/scheduler.py (`add_scheduler_cmd`)                                                 | implemented | manual-only    |
| docs/cli/commands.md:255                     | `soniq migrate-status`          | soniq/cli/migrate_status.py:11                                                               | implemented | auto-smoke     |
| README.md:34                                 | `soniq setup`                   | soniq/cli/setup.py:11                                                                        | implemented | manual-only    |
| README.md:35                                 | `soniq worker --concurrency`     | soniq/cli/worker.py                                                                          | implemented | manual-only    |
| README.md:124                                | `soniq dashboard`               | soniq/cli/dashboard.py                                                                       | implemented | manual-only    |
| CHANGELOG.md:81                              | `soniq scheduler` sidecar       | soniq/cli/scheduler.py                                                                       | implemented | manual-only    |

## Environment variables (settings)

Each of these maps to a `Field(...)` on `SoniqSettings` (env prefix `SONIQ_`).

| Source (file:line)                              | Symbol                             | Code reference                                            | Status      | Smoke category |
| ----------------------------------------------- | ---------------------------------- | --------------------------------------------------------- | ----------- | -------------- |
| docs/cli/commands.md:3                          | `SONIQ_DATABASE_URL`               | soniq/settings.py:65 (`database_url`)                     | implemented | manual-only    |
| docs/cli/commands.md:48                         | `SONIQ_JOBS_MODULES`               | soniq/settings.py:74 (`jobs_modules`)                     | implemented | manual-only    |
| docs/getting-started/quickstart.md:42           | `SONIQ_JOBS_MODULES`               | soniq/settings.py:74                                      | implemented | manual-only    |
| docs/production/checklist.md (concurrency rows) | `SONIQ_CONCURRENCY`                | soniq/settings.py:83 (`concurrency`)                      | implemented | manual-only    |
| docs/production/checklist.md                    | `SONIQ_QUEUES`                     | soniq/settings.py:87 (`queues`, custom comma-list source) | implemented | manual-only    |
| docs/production/checklist.md                    | `SONIQ_MAX_RETRIES`                | soniq/settings.py:93 (`max_retries`)                      | implemented | manual-only    |
| docs/production/checklist.md                    | `SONIQ_PRIORITY`                   | soniq/settings.py:100 (`priority`)                        | implemented | manual-only    |
| docs/guides/cross-service-jobs.md:59            | `SONIQ_ENQUEUE_VALIDATION`         | soniq/settings.py:107 (`enqueue_validation`)              | implemented | manual-only    |
| docs/guides/cross-service-jobs.md:86            | `SONIQ_TASK_NAME_PATTERN`          | soniq/settings.py:118 (`task_name_pattern`)               | implemented | manual-only    |
| docs/concepts/tasks-vs-jobs.md:25               | `SONIQ_TASK_NAME_PATTERN`          | soniq/settings.py:118                                     | implemented | manual-only    |
| docs/production/reliability.md:124              | `SONIQ_HEARTBEAT_INTERVAL`         | soniq/settings.py:152 (`heartbeat_interval`)              | implemented | manual-only    |
| docs/production/checklist.md:148                | `SONIQ_WORKER_HEARTBEAT_INTERVAL`  | (no field; doc uses `heartbeat_interval` symbol)          | removed     | manual-only    |
| docs/production/reliability.md:124              | `SONIQ_STALE_WORKER_THRESHOLD`     | (no field; closest is `heartbeat_timeout`)                | removed     | manual-only    |
| docs/production/checklist.md:38                 | `SONIQ_STALE_WORKER_THRESHOLD`     | (no field)                                                | removed     | manual-only    |
| docs/production/checklist.md:121                | `SONIQ_STUCK_JOBS_THRESHOLD`       | (no field)                                                | removed     | manual-only    |
| docs/production/checklist.md:122                | `SONIQ_JOB_FAILURE_RATE_THRESHOLD` | (no field)                                                | removed     | manual-only    |
| docs/production/checklist.md:123                | `SONIQ_MEMORY_USAGE_THRESHOLD`     | (no field)                                                | removed     | manual-only    |
| docs/production/checklist.md:124                | `SONIQ_DISK_USAGE_THRESHOLD`       | (no field)                                                | removed     | manual-only    |
| docs/production/checklist.md:125                | `SONIQ_CPU_USAGE_THRESHOLD`        | (no field)                                                | removed     | manual-only    |
| docs/production/reliability.md                  | `SONIQ_HEARTBEAT_TIMEOUT`          | soniq/settings.py:173 (`heartbeat_timeout`)               | implemented | manual-only    |
| docs/cli/commands.md                            | `SONIQ_JOB_TIMEOUT`                | soniq/settings.py:159 (`job_timeout`)                     | implemented | manual-only    |
| docs/production/checklist.md                    | `SONIQ_CLEANUP_INTERVAL`           | soniq/settings.py:166 (`cleanup_interval`)                | implemented | manual-only    |
| docs/production/checklist.md                    | `SONIQ_POLL_INTERVAL`              | soniq/settings.py:180 (`poll_interval`)                   | implemented | manual-only    |
| docs/production/checklist.md                    | `SONIQ_ERROR_RETRY_DELAY`          | soniq/settings.py:187 (`error_retry_delay`)               | implemented | manual-only    |
| docs/production/checklist.md                    | `SONIQ_POOL_MIN_SIZE`              | soniq/settings.py:203 (`pool_min_size`)                   | implemented | manual-only    |
| docs/production/checklist.md                    | `SONIQ_POOL_MAX_SIZE`              | soniq/settings.py:207 (`pool_max_size`)                   | implemented | manual-only    |
| docs/production/checklist.md                    | `SONIQ_POOL_HEADROOM`              | soniq/settings.py:211 (`pool_headroom`)                   | implemented | manual-only    |
| docs/production/checklist.md                    | `SONIQ_LOG_LEVEL`                  | soniq/settings.py:219 (`log_level`)                       | implemented | manual-only    |
| docs/cli/commands.md                            | `SONIQ_RESULT_TTL`                 | soniq/settings.py:229 (`result_ttl`)                      | implemented | manual-only    |
| docs/production/checklist.md                    | `SONIQ_DEBUG`                      | soniq/settings.py:243 (`debug`)                           | implemented | manual-only    |
| docs/production/checklist.md                    | `SONIQ_ENVIRONMENT`                | soniq/settings.py:256 (`environment`)                     | implemented | manual-only    |

## Feature flags (env vars consulted directly, not via `SoniqSettings`)

| Source (file:line)                      | Symbol                             | Code reference                                                                         | Status      | Smoke category |
| --------------------------------------- | ---------------------------------- | -------------------------------------------------------------------------------------- | ----------- | -------------- |
| docs/cli/commands.md:172                | `SONIQ_DASHBOARD_ENABLED`          | (no code reference; documented as opt-in flag but not gated in soniq/cli/dashboard.py) | removed     | manual-only    |
| docs/dashboard/overview.md:50           | `SONIQ_DASHBOARD_WRITE_ENABLED`    | soniq/dashboard/server.py:774 (UI hint only; no Python gate)                      | removed     | manual-only    |
| docs/dashboard/overview.md:128          | `SONIQ_DASHBOARD_API_KEY`          | soniq/dashboard/server.py:60, :129                                                | implemented | manual-only    |
| docs/cli/commands.md:200                | `SONIQ_SCHEDULING_ENABLED`         | (no code reference)                                                                    | removed     | manual-only    |
| docs/cli/commands.md:113                | `SONIQ_DEAD_LETTER_QUEUE_ENABLED`  | (no code reference)                                                                    | removed     | manual-only    |
| docs/getting-started/installation.md:77 | `SONIQ_TIMEOUTS_ENABLED`           | (no code reference)                                                                    | removed     | manual-only    |
| docs/getting-started/installation.md:79 | `SONIQ_LOGGING_ENABLED`            | (no code reference)                                                                    | removed     | manual-only    |
| docs/getting-started/installation.md:80 | `SONIQ_WEBHOOKS_ENABLED`           | (no code reference)                                                                    | removed     | manual-only    |
| docs/getting-started/installation.md:81 | `SONIQ_SIGNING_ENABLED`            | (no code reference)                                                                    | removed     | manual-only    |
| docs/plugins/authoring.md:115           | `SONIQ_PLUGINS` / `SONIQ_MY_PLUGIN_*` | soniq/cli/main.py:103 (`SONIQ_PLUGINS`); per-plugin convention for the suffix       | implemented | manual-only    |
| docs/production/deployment.md:45        | `SONIQ_SCHEDULER_SUPPRESS_WARNING` | soniq/app.py:1208                                                                      | implemented | manual-only    |

## Error codes referenced as identifiers

| Source (file:line)                          | Symbol                          | Code reference                                                  | Status      | Smoke category |
| ------------------------------------------- | ------------------------------- | --------------------------------------------------------------- | ----------- | -------------- |
| docs/guides/cross-service-jobs.md:64        | `SONIQ_UNKNOWN_TASK_NAME`       | soniq/errors.py                                                 | implemented | manual-only    |
| docs/guides/cross-service-jobs.md:90        | `SONIQ_INVALID_TASK_NAME`       | soniq/errors.py (consumed in soniq/core/naming.py:38)           | implemented | manual-only    |
| docs/recipes/cross-service-task-stubs.md:97 | `SONIQ_TASK_ARGS_INVALID`       | soniq/errors.py                                                 | implemented | manual-only    |
| docs/plugins/authoring.md:100               | `SONIQ_PLUGIN_DUPLICATE`        | soniq/errors.py                                                 | implemented | manual-only    |
| docs/design/dlq_option_a.md:76              | `SONIQ_ALLOW_LEGACY_DLQ_STATUS` | (intentionally documented as dropped in v6+; no code reference) | removed     | manual-only    |

## Deployment manifests

These shell out to the documented commands above; the audit covers the symbols they invoke, not every line of YAML.

| Source (file:line)                 | Symbol                            | Code reference                             | Status      | Smoke category |
| ---------------------------------- | --------------------------------- | ------------------------------------------ | ----------- | -------------- |
| deployment/soniq-worker.service    | `soniq worker`                     | soniq/cli/worker.py                         | implemented | manual-only    |
| deployment/soniq-scheduler.service | `soniq scheduler`                 | soniq/cli/scheduler.py                     | implemented | manual-only    |
| deployment/soniq-dashboard.service | `soniq dashboard`                 | soniq/cli/dashboard.py                     | implemented | manual-only    |
| deployment/supervisor.conf         | `soniq worker` / `soniq scheduler` | soniq/cli/worker.py, soniq/cli/scheduler.py | implemented | manual-only    |
| deployment/kubernetes.yaml         | `soniq worker`                     | soniq/cli/worker.py                         | implemented | manual-only    |
| deployment/docker-compose.yml      | `soniq worker` / `soniq scheduler` | soniq/cli/worker.py, soniq/cli/scheduler.py | implemented | manual-only    |
| deployment/Dockerfile.worker       | `soniq worker`                     | soniq/cli/worker.py                         | implemented | manual-only    |
| deployment/Dockerfile.dashboard    | `soniq dashboard`                 | soniq/cli/dashboard.py                     | implemented | manual-only    |

## Contract artifacts

Each contract doc has a code anchor and a contract test. New contracts add a row here rather than mutating the CLI/env tables above.

| Contract doc                               | Code anchor                                                                                                      | Contract test                                                                            | Status      |
| ------------------------------------------ | ---------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- | ----------- |
| docs/_internals/contracts/queue_stats.md              | soniq/types.py (`QueueStats`); 3 backends' `get_queue_stats`                                                     | tests/contract/test_queue_stats_keys.py                                                  | implemented |
| docs/_internals/contracts/dead_letter.md              | 3 backends' `mark_job_dead_letter`; soniq/features/dead_letter.py (`move_job_to_dead_letter` deleted)            | tests/integration/dlq/test_dlq_contract.py (Group A x 3 backends, Group B postgres-only) | implemented |
| docs/_internals/design/dlq_option_a.md     | soniq/backends/postgres/migrations/0001_core.sql (status CHECK); soniq/backends/postgres/migrations/0002_dead_letter.sql (DLQ table) | tests/integration/dlq/test_dlq_contract.py (Group B)                                     | implemented |

## Notes

- Rows with `Status = removed` are the doc-cleanup punch list: delete the doc reference or replace it with the existing field name (e.g., `SONIQ_STALE_WORKER_THRESHOLD` -> `SONIQ_HEARTBEAT_TIMEOUT`).
- `Source (file:line)` points to a representative occurrence. Symbols mentioned in many places (e.g. `SONIQ_DATABASE_URL`) appear once; the canonical reference is the table in `docs/production/checklist.md` or the install guide.
- `Smoke category` choice is conservative: anything that could mutate the database, run for more than a few seconds, or touch external services is `manual-only`. The `auto-smoke` set is deliberately small so the CI job stays fast and reliable.
- Long-running commands (`soniq worker`, `soniq scheduler`, `soniq dashboard`) are `manual-only` even though they accept `--help`. The smoke job invokes `--help` separately if it wants to assert the parser builds; the rows above describe the _command_ itself.
