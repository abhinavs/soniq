---
description: Python API reference for Soniq, the PostgreSQL job queue for Python. Pages cover the Soniq class, jobs, workers, hooks and the dead-letter queue.
---

# Soniq Python API reference

This section documents the public Python API of Soniq, a background-job library for Python that stores jobs in PostgreSQL. Each page lists signatures, parameters, defaults and return types.

- [Soniq class](soniq.md) - the constructor, settings, lifecycle and job lookup methods
- [Jobs](jobs.md) - `@soniq.job()`, `enqueue()`, `schedule()`, `@soniq.periodic()`, `JobContext`, `JobStatus`
- [Worker](worker.md) - `run_worker()`, worker settings, concurrency and graceful shutdown
- [Hooks](hooks.md) - `@soniq.before_job`, `@soniq.after_job`, `@soniq.on_error` and middleware
- [Dead-letter queue](dead-letter.md) - the `soniq.dead_letter` service: list, replay, bulk operations, stats

For the command line, see the [CLI reference](../cli/commands.md).
