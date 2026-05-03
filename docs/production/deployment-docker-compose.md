# Docker Compose

Good for staging or small production environments. File: `deployment/docker-compose.yml`.

```yaml
version: "3.8"

services:
  postgres:
    image: postgres:15-alpine
    restart: always
    environment:
      POSTGRES_DB: soniq_prod
      POSTGRES_USER: soniq
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-changeme}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U soniq -d soniq_prod"]
      interval: 10s
      timeout: 5s
      retries: 5

  soniq_worker:
    build:
      context: .
      dockerfile: Dockerfile.worker
    restart: always
    depends_on:
      postgres:
        condition: service_healthy
    environment:
      SONIQ_DATABASE_URL: postgresql://soniq:${POSTGRES_PASSWORD:-changeme}@postgres:5432/soniq_prod
      SONIQ_JOBS_MODULES: myapp.jobs
    command: ["soniq", "worker", "--concurrency=4"]
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
      postgres:
        condition: service_healthy
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
      postgres:
        condition: service_healthy
    environment:
      SONIQ_DATABASE_URL: postgresql://soniq:${POSTGRES_PASSWORD:-changeme}@postgres:5432/soniq_prod
    ports:
      - "8000:8000"
    command: ["soniq", "dashboard", "--host=0.0.0.0", "--port=8000"]

volumes:
  postgres_data:
```

## Scaling workers

```bash
docker-compose up -d --scale soniq_worker=3
```

The scheduler should stay at one replica; multiple instances coordinate via an advisory lock and the runners-up just idle, so scaling it is harmless but not useful.

## See also

- [Deployment overview](deployment.md) - prerequisites, queue routing, performance tuning
- [Reliability](reliability.md) - graceful shutdown, stuck-job recovery
