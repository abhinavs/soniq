# Deployment

Pick a process supervisor, drop in the config for your shape, run `soniq setup` once. That is the whole story.

Ready-to-use configuration files live in the [`deployment/`](../../deployment/) directory of the repo.

## Pick a shape

| Shape | Use when | Config |
|---|---|---|
| [Systemd](deployment-systemd.md) | Modern Linux servers, direct process control | `deployment/soniq-*.service` |
| [Docker Compose](deployment-docker-compose.md) | Staging or small single-host production | `deployment/docker-compose.yml` |
| [Kubernetes](deployment-kubernetes.md) | Container platforms, autoscaling | `deployment/kubernetes.yaml` |
| [Supervisor](deployment-supervisor.md) | Older setups, shared environments | `deployment/supervisor.conf` |

The four shapes are interchangeable. Soniq itself does not care which one supervises it - all it asks for is `SIGTERM` for graceful stop and a long enough grace window.

## Prerequisites

**Minimum:**

- Python 3.10+
- PostgreSQL 12+
- 2 GB RAM, 2 CPU cores

**Recommended for production:**

- Python 3.12+
- PostgreSQL 15+
- 4 GB+ RAM, 4+ CPU cores
- SSD storage for the database

### Database setup

```bash
createdb soniq_prod
psql -c "CREATE USER soniq WITH PASSWORD 'your_secure_password';"
psql -c "GRANT ALL PRIVILEGES ON DATABASE soniq_prod TO soniq;"

export SONIQ_DATABASE_URL="postgresql://soniq:your_secure_password@localhost/soniq_prod"
soniq setup
```

Run `soniq setup` once per deploy, not from every replica's startup. See [going to production](going-to-production.md).

### Application user (Linux)

```bash
sudo useradd --system --create-home --shell /bin/bash soniq
sudo mkdir -p /opt/soniq /var/log/soniq
sudo chown soniq:soniq /opt/soniq /var/log/soniq
```

## Recurring jobs require a scheduler sidecar

If your application uses `@app.periodic(...)` jobs, deploy a separate `soniq scheduler` process alongside `soniq worker`. The worker process **does not** evaluate due recurring jobs - that responsibility lives with the scheduler so worker scaling does not duplicate scheduler work.

If `soniq worker` finds `@periodic` decorators registered and no scheduler-sidecar process holds the leadership lock, it prints a one-time WARN at startup. To silence the WARN once you have configured the sidecar (or if you intentionally do not run recurring jobs), set `SONIQ_SCHEDULER_SUPPRESS_WARNING=1` in the worker environment.

The scheduler is a standard subcommand (`soniq scheduler`); it is not a separate package and shares the same `soniq` CLI entry point. Multiple instances coordinate via the `soniq.maintenance` Postgres advisory lock - duplicates are safe but only one is needed.

The shipped deployment templates include the sidecar:

- Systemd: `deployment/soniq-scheduler.service`
- Docker Compose: the `soniq_scheduler` service in `deployment/docker-compose.yml`
- Kubernetes: the `soniq-scheduler` Deployment in `deployment/kubernetes.yaml`
- Supervisor: the `[program:soniq_scheduler]` block in `deployment/supervisor.conf`

## Queue routing

When different queues have different throughput or latency needs, run separate worker processes per queue group. Each scales independently.

```bash
# Email workers - high concurrency, IO-bound
soniq worker --concurrency=8 --queues=emails,notifications

# Media workers - low concurrency, CPU-bound
soniq worker --concurrency=2 --queues=media,transcode
```

In Kubernetes, use separate Deployments. In Docker Compose, use separate services. In Supervisor, use separate `[program:]` blocks. See the `deployment/` directory for examples with queue routing already configured.

## Performance tuning

### Worker sizing

- **Memory:** 512 MB per worker process minimum. Jobs with large in-memory data need more.
- **CPU:** 1 core per 4 concurrent jobs is a reasonable starting point. CPU-bound jobs need dedicated cores.
- **Concurrency:** Start with 4, measure, adjust. IO-bound workloads (HTTP calls, email sending) can go to 16-32. CPU-bound workloads should stay at 1-2 per core.

### Graceful shutdown

Always stop workers with `SIGTERM`, not `SIGKILL`. Soniq handles `SIGTERM` by finishing in-flight jobs before exiting.

- **Systemd:** Set `TimeoutStopSec` to match your longest job timeout plus a buffer.
- **Kubernetes:** Set `terminationGracePeriodSeconds` the same way. Default is 30s, which is too short for most production workloads.
- **Supervisor:** Set `stopwaitsecs` in the program config.

If a worker is killed with `SIGKILL` (or OOM-killed), its in-flight jobs become stuck. See the [reliability guide](reliability.md) for recovery.

### Database connection pressure

Each worker process maintains its own connection pool. With many workers, total connections add up fast.

```
total_connections = num_workers * pool_max_size
```

Make sure your PostgreSQL `max_connections` can handle this, with room for your application and admin connections. See [PostgreSQL tuning](postgres.md) for pool sizing details.
