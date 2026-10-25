---
description: Deploy Soniq workers, the scheduler and the dashboard on PostgreSQL with systemd, Docker Compose, Kubernetes or Supervisor, plus queue routing and sizing.
---

# Deploy Soniq workers and the scheduler

Pick a process manager, use the config for your shape, and run `soniq setup` once per deploy. Soniq does not care which manager supervises it. It needs `SIGTERM` for a graceful stop and a long enough grace window.

Ready-to-use configuration files are in the [`deployment/`](https://github.com/abhinavs/soniq/tree/main/deployment) directory of the repo.

## Pick a shape

| Shape | Use when | Config |
|---|---|---|
| [Systemd](deployment-systemd.md) | Modern Linux servers, direct process control | `deployment/soniq-*.service` |
| [Docker Compose](deployment-docker-compose.md) | Staging or small single-host production | `deployment/docker-compose.yml` |
| [Kubernetes](deployment-kubernetes.md) | Container platforms, autoscaling | `deployment/kubernetes.yaml` |
| [Supervisor](deployment-supervisor.md) | Older setups, shared environments | `deployment/supervisor.conf` |

The four shapes are interchangeable. For services that only enqueue jobs, see [deployment shapes](deployment-shapes.md).

## Prerequisites

**Minimum:**

- Python 3.10+
- PostgreSQL 15+ (CI runs 15 and 18; `soniq setup` warns on 14 and older)
- 2 GB RAM, 2 CPU cores

**Recommended for production:**

- Python 3.12+
- PostgreSQL 18
- 4 GB+ RAM, 4+ CPU cores
- SSD storage for the database

### Database setup

```bash
createdb soniq_prod
psql -c "CREATE USER soniq WITH PASSWORD 'your_secure_password';"
psql -c "ALTER DATABASE soniq_prod OWNER TO soniq;"

export SONIQ_DATABASE_URL="postgresql://soniq:your_secure_password@localhost/soniq_prod"
soniq setup
```

On PostgreSQL 15 and newer, `GRANT ALL PRIVILEGES ON DATABASE` is not enough, because the `public` schema is no longer writable by every role. The `ALTER DATABASE ... OWNER` command above gives the `soniq` role the schema rights `soniq setup` needs. Run `soniq setup` once per deploy, not from every replica's startup. See [going to production](going-to-production.md).

### Application user (Linux)

```bash
sudo useradd --system --create-home --shell /bin/bash soniq
sudo mkdir -p /opt/soniq /var/log/soniq
sudo chown soniq:soniq /opt/soniq /var/log/soniq
```

## Recurring jobs require a scheduler sidecar

If your application uses `@soniq.periodic(...)` jobs, deploy a separate `soniq scheduler` process alongside `soniq worker`. The worker process **does not** evaluate due recurring jobs - that responsibility lives with the scheduler so worker scaling does not duplicate scheduler work.

If `soniq worker` finds `@periodic` decorators and no scheduler, it prints a one-time warning at startup. To silence the warning, set `SONIQ_SCHEDULER_SUPPRESS_WARNING=1` in the worker environment. Do this after you configure the sidecar, or if you intentionally run no recurring jobs.

The scheduler is the `soniq scheduler` subcommand. It is part of the same `soniq` package. Extra instances are safe because they take turns as leader through a Postgres advisory lock, but one instance is enough.

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

In Kubernetes, use separate Deployments. In Docker Compose, use separate services. In Supervisor, use separate `[program:]` blocks. The `deployment/` directory has examples with queue routing.

## Performance tuning

### Worker sizing

- **Memory:** 512 MB per worker process minimum. Jobs with large in-memory data need more.
- **CPU:** 1 core per 4 concurrent jobs is a reasonable starting point. CPU-bound jobs need dedicated cores.
- **Concurrency:** Start with 4, measure, adjust. IO-bound workloads (HTTP calls, email sending) can go to 16-32. CPU-bound workloads should stay at 1-2 per core.

### Graceful shutdown

Stop workers with `SIGTERM`, not `SIGKILL`. On `SIGTERM`, the worker stops claiming jobs and waits for in-flight jobs for up to `SONIQ_SHUTDOWN_TIMEOUT` seconds (default 30). After that, it returns unfinished async jobs to `queued`. Sync handlers get a further `SONIQ_SYNC_HANDLER_GRACE_SECONDS` (default: the job timeout). To let long async jobs finish, raise `SONIQ_SHUTDOWN_TIMEOUT` and the grace window below together.

- **systemd:** Set `TimeoutStopSec` to your longest job timeout plus a buffer.
- **Kubernetes:** Set `terminationGracePeriodSeconds` the same way. The default is 30 seconds, which is too short for most production workloads.
- **Supervisor:** Set `stopwaitsecs` in the program config.

A worker that receives `SIGKILL` or an out-of-memory kill leaves its in-flight jobs stuck in `processing`. See the [reliability guide](reliability.md) for recovery.

### Database connection pressure

Each worker process maintains its own connection pool. With many workers, total connections add up fast.

```
total_connections = num_workers * pool_max_size
```

Make sure your PostgreSQL `max_connections` can handle this, with room for your application and admin connections. See [PostgreSQL tuning](postgres.md) for pool sizing details.

## Next steps

- [Going to production](going-to-production.md) - the full checklist
- [Reliability](reliability.md) - crash recovery and delivery guarantees
