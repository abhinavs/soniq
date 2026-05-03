# Supervisor

Good for older setups or shared environments. File: `deployment/supervisor.conf`.

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
stopwaitsecs=310
redirect_stderr=true
stdout_logfile=/var/log/soniq/worker.log
stdout_logfile_maxbytes=10MB
stdout_logfile_backups=5
environment=SONIQ_DATABASE_URL="postgresql://soniq:password@localhost/soniq_prod",SONIQ_LOG_LEVEL="INFO",SONIQ_JOBS_MODULES="myapp.jobs"

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
command=/opt/soniq/venv/bin/soniq dashboard --host=0.0.0.0 --port=8000
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

## Managing with Supervisor

```bash
sudo cp deployment/supervisor.conf /etc/supervisor/conf.d/soniq.conf
sudo supervisorctl reread
sudo supervisorctl update
sudo supervisorctl start soniq:*
sudo supervisorctl status
```

Set `stopwaitsecs` on every worker program to at least your longest job timeout plus a buffer. Default (10s) is too short - workers will be `SIGKILL`ed mid-job and their in-flight rows will end up stuck in `processing` until the heartbeat sweep recovers them.

## See also

- [Deployment overview](deployment.md) - prerequisites, queue routing, performance tuning
- [Reliability](reliability.md) - graceful shutdown, stuck-job recovery
