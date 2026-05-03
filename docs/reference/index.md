# Reference

*Complete API, CLI, and configuration docs.* Look-up material - not designed to be read end-to-end. If you are learning Soniq for the first time, the [tutorial](../tutorial/01-defining-jobs.md) is a better starting point.

## Python API

- [Soniq](../api/soniq.md) - the application object
- [Jobs](../api/jobs.md) - the `@app.job` decorator and `enqueue` surface
- [Worker](../api/worker.md) - `run_worker` and worker configuration
- [Hooks](../api/hooks.md) - `before_job`, `after_job`, `on_error`

## CLI

- [Commands](../cli/commands.md) - `setup`, `worker`, `scheduler`, `dead-letter`, `status`, `inspect`, `dashboard`

## Operational reference

- [Dead-letter queue](dead-letter.md) - how the DLQ works and how to inspect it
- [Glossary](glossary.md) - one-paragraph definitions for the words the docs use

## Migration

- [Migrating to Soniq](../migration/index.md) - tradeoffs and shared facts
- [From Celery](../migration/from-celery.md) - concept map, retry/result/Beat equivalents, module-at-a-time sequence
- [From RQ](../migration/from-rq.md) - concept map, four-state lifecycle vs registries, mechanical rewrite

## Dashboard

- [Overview](../dashboard/overview.md) - the bundled web UI for inspecting queues
