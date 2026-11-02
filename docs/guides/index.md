---
description: Task-focused Soniq guides for Python background jobs on PostgreSQL - FastAPI integration, transactional enqueue, testing, common patterns and cross-service jobs.
---

# Soniq guides for FastAPI, testing and transactional enqueue

These guides show how to do one job with Soniq, a Python background job library backed by PostgreSQL. Pick the guide that matches what you are about to build.

- [FastAPI integration](fastapi.md) - lifespan management, enqueue from routes, run workers as separate processes
- [Transactional enqueue](transactional-enqueue.md) - enqueue jobs atomically inside database transactions
- [Common patterns](common-patterns.md) - hooks, job context, deduplication, job results
- [Testing](testing.md) - memory backend for unit tests, SQLite for integration tests, fixtures and isolation
- [Cross-service jobs](cross-service-jobs.md) - enqueue a job by name that another service runs
