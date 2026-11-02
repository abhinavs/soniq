---
description: Run Soniq workers, the scheduler and the dashboard with Docker Compose and PostgreSQL 18 for a Python background job queue. Compose file and worker scaling.
---

# Run Soniq with Docker Compose

Docker Compose suits staging and small single-host production. The repo has the full file at `deployment/docker-compose.yml`, with `Dockerfile.worker` and `Dockerfile.dashboard` next to it. The example below is a condensed version. It runs PostgreSQL 18, a worker, a scheduler and the dashboard.

```yaml
services:
  postgres:
    image: postgres:18-alpine
    restart: always
    environment:
      POSTGRES_DB: soniq_prod
      POSTGRES_USER: soniq
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-changeme}
    volumes:
      - postgres_data:/var/lib/postgresql
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U soniq -d soniq_prod"]
      interval: 10s
      timeout: 5s
      retries: 5

  # Applies Soniq's migrations, then exits.
  soniq_setup:
    build:
      context: .
      dockerfile: Dockerfile.worker
    depends_on:
      postgres:
        condition: service_healthy
    environment:
      SONIQ_DATABASE_URL: postgresql://soniq:${POSTGRES_PASSWORD:-changeme}@postgres:5432/soniq_prod
    command: ["soniq", "setup"]
    restart: "no"

  soniq_worker:
    build:
      context: .
      dockerfile: Dockerfile.worker
    restart: always
    depends_on:
      soniq_setup:
        condition: service_completed_successfully
    environment:
      SONIQ_DATABASE_URL: postgresql://soniq:${POSTGRES_PASSWORD:-changeme}@postgres:5432/soniq_prod
      SONIQ_JOBS_MODULES: myapp.jobs
      SONIQ_SCHEDULER_SUPPRESS_WARNING: "1"
    command: ["soniq", "worker", "--concurrency=4"]
    stop_grace_period: 45s  # longer than SONIQ_SHUTDOWN_TIMEOUT (30s)
    deploy:
      resources:
        limits:
          memory: 512M
          cpus: "1.0"

  soniq_scheduler:
    build:
      context: .
      dockerfile: Dockerfile.worker
    restart: always
    depends_on:
      soniq_setup:
        condition: service_completed_successfully
    environment:
      SONIQ_DATABASE_URL: postgresql://soniq:${POSTGRES_PASSWORD:-changeme}@postgres:5432/soniq_prod
      SONIQ_JOBS_MODULES: myapp.jobs
    command: ["soniq", "scheduler"]

  soniq_dashboard:
    build:
      context: .
      dockerfile: Dockerfile.dashboard
    restart: always
    depends_on:
      soniq_setup:
        condition: service_completed_successfully
    environment:
      SONIQ_DATABASE_URL: postgresql://soniq:${POSTGRES_PASSWORD:-changeme}@postgres:5432/soniq_prod
      SONIQ_DASHBOARD_API_KEY: ${SONIQ_DASHBOARD_API_KEY:?set SONIQ_DASHBOARD_API_KEY}
    ports:
      - "127.0.0.1:6161:6161"
    command: ["soniq", "dashboard", "--host=0.0.0.0", "--port=6161"]

volumes:
  postgres_data:
```

The `soniq_setup` service runs `soniq setup` and exits. The worker, scheduler and dashboard start only after it succeeds.

The dashboard listens on `0.0.0.0` inside its container, so it needs `SONIQ_DASHBOARD_API_KEY`. Without a key, the dashboard accepts only `localhost` Host headers. Clients send the key in the `X-API-Key` header. Open the dashboard at `http://localhost:6161`.

## Scale workers

```bash
docker compose up -d --scale soniq_worker=3
```

Keep the scheduler at one replica. Extra instances coordinate through an advisory lock and the runners-up idle, so they do no harm but add nothing.

> **Warning:** Docker sends `SIGKILL` after `stop_grace_period`, which is 10 seconds by default. A worker drains for up to `SONIQ_SHUTDOWN_TIMEOUT` (30 seconds by default), then requeues the jobs that are still running. Set `stop_grace_period` longer than `SONIQ_SHUTDOWN_TIMEOUT`. If you raise the timeout for long jobs, raise the grace period too.

## See also

- [Deployment overview](deployment.md) - prerequisites, queue routing, performance tuning
- [Reliability](reliability.md) - graceful shutdown, stuck-job recovery
