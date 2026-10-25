---
description: Run Soniq, the PostgreSQL job queue for Python, in production. Deployment recipes, pool sizing, delivery guarantees, Alembic migrations and upgrade steps.
---

# Run Soniq in production

These pages show how to deploy Soniq workers, the scheduler and the dashboard on PostgreSQL, and how to keep them healthy. Start with the checklist, then pick a deployment recipe.

- [Going to production](going-to-production.md) - the checklist and the common mistakes
- [Deployment](deployment.md) - systemd, Docker Compose, Kubernetes and Supervisor configurations
- [Deployment shapes](deployment-shapes.md) - producer services, consumer services and shared task stubs
- [PostgreSQL tuning](postgres.md) - connection pool sizing, PgBouncer and LISTEN/NOTIFY
- [Reliability](reliability.md) - delivery guarantees, idempotency, stuck job recovery and failure modes
- [Migrations with Alembic](migrations.md) - export the Soniq schema as an Alembic revision
- [Upgrading from 0.1.0](upgrading.md) - migration 0006, deploy order and rollback
