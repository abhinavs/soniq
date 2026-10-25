---
description: Run Soniq workers, the scheduler and the dashboard under Supervisor with a PostgreSQL job queue. Program config, log rotation and graceful stop settings.
---

# Run Soniq workers under Supervisor

Supervisor suits older setups and shared hosts. The repo has the full file at `deployment/supervisor.conf`. The example below is condensed to a worker, a scheduler and a dashboard. Run `soniq setup` once per deploy before you start the programs.

```ini
[group:soniq]
programs=soniq_worker,soniq_scheduler,soniq_dashboard

[program:soniq_worker]
command=/opt/soniq/venv/bin/soniq worker --concurrency=4
directory=/opt/soniq
user=soniq
autostart=true
autorestart=true
startretries=3
stopsignal=TERM
stopwaitsecs=45
redirect_stderr=true
stdout_logfile=/var/log/soniq/worker.log
stdout_logfile_maxbytes=10MB
stdout_logfile_backups=5
environment=SONIQ_DATABASE_URL="postgresql://soniq:password@localhost/soniq_prod",SONIQ_LOG_LEVEL="INFO",SONIQ_JOBS_MODULES="myapp.jobs",SONIQ_SCHEDULER_SUPPRESS_WARNING="1"

[program:soniq_scheduler]
command=/opt/soniq/venv/bin/soniq scheduler
directory=/opt/soniq
user=soniq
autostart=true
autorestart=true
startretries=3
redirect_stderr=true
stdout_logfile=/var/log/soniq/scheduler.log
stdout_logfile_maxbytes=10MB
stdout_logfile_backups=5
environment=SONIQ_DATABASE_URL="postgresql://soniq:password@localhost/soniq_prod",SONIQ_LOG_LEVEL="INFO",SONIQ_JOBS_MODULES="myapp.jobs"

[program:soniq_dashboard]
command=/opt/soniq/venv/bin/soniq dashboard --host=127.0.0.1 --port=6161
directory=/opt/soniq
user=soniq
autostart=true
autorestart=true
startretries=3
redirect_stderr=true
stdout_logfile=/var/log/soniq/dashboard.log
stdout_logfile_maxbytes=10MB
stdout_logfile_backups=5
environment=SONIQ_DATABASE_URL="postgresql://soniq:password@localhost/soniq_prod"
```

## Manage the programs

```bash
sudo cp deployment/supervisor.conf /etc/supervisor/conf.d/soniq.conf
sudo supervisorctl reread
sudo supervisorctl update
sudo supervisorctl start soniq:*
sudo supervisorctl status
```

> **Warning:** Set `stopwaitsecs` on every worker program to more than `SONIQ_SHUTDOWN_TIMEOUT` (30 seconds by default). The Supervisor default is 10 seconds. With that default, Supervisor sends `SIGKILL` to workers mid-job, and their in-flight rows stay in `processing` until the heartbeat sweep recovers them. If you raise `SONIQ_SHUTDOWN_TIMEOUT` for long jobs, raise `stopwaitsecs` too.

The dashboard example binds to `127.0.0.1`, so put a reverse proxy in front of it. To bind to `0.0.0.0`, also set `SONIQ_DASHBOARD_API_KEY`.

## See also

- [Deployment overview](deployment.md) - prerequisites, queue routing, performance tuning
- [Reliability](reliability.md) - graceful shutdown, stuck-job recovery
