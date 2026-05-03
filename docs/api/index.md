# API Reference

Complete reference for Soniq's Python API.

- [Soniq](soniq.md) — the main class, global configuration, lifecycle management
- [Jobs](jobs.md) — `@job` decorator, `enqueue()`, `schedule()`, `JobContext`, `JobStatus`
- [Worker](worker.md) — `run_worker()`, worker configuration, concurrency
- [Hooks](hooks.md) — `@before_job`, `@after_job`, `@on_error` middleware
