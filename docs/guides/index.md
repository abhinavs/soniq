# Guides

*Task-specific how-tos for common setups.* Read these when you are about to do a particular thing - integrate FastAPI, write tests, enqueue inside a transaction - and want a focused walkthrough.

- [FastAPI integration](fastapi.md) — lifespan management, enqueuing from routes, running workers as separate processes
- [Transactional enqueue](transactional-enqueue.md) — enqueue jobs atomically inside database transactions
- [Common patterns](common-patterns.md) — middleware hooks, deduplication, argument validation, job results
- [Testing](testing.md) — memory backend for unit tests, SQLite for integration tests, fixtures and isolation
