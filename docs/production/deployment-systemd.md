---
description: Run Soniq workers, the scheduler and the dashboard as systemd services on Linux with a PostgreSQL job queue. Unit files, hardening and graceful shutdown.
---

# Run Soniq workers under systemd

systemd suits modern Linux servers where you want direct process control. The repo has three unit files in `deployment/`: `soniq-worker.service`, `soniq-scheduler.service` and `soniq-dashboard.service`. The examples below are condensed versions with the settings that matter.

## Worker service

```ini
[Unit]
Description=Soniq Worker
After=network.target

[Service]
Type=exec
User=soniq
Group=soniq
WorkingDirectory=/opt/soniq
Environment=SONIQ_DATABASE_URL=postgresql://soniq:password@localhost/soniq_prod
Environment=SONIQ_LOG_LEVEL=INFO
Environment=SONIQ_JOBS_MODULES=myapp.jobs
ExecStart=/opt/soniq/venv/bin/soniq worker --concurrency=4
KillMode=mixed
Restart=always
RestartSec=5

# Security hardening
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/opt/soniq /var/log/soniq

# Resource limits
MemoryMax=512M
CPUQuota=200%

# SIGTERM starts a drain of up to SONIQ_SHUTDOWN_TIMEOUT (default 30s).
# Wait longer than that before systemd sends SIGKILL.
TimeoutStopSec=45

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=soniq-worker

[Install]
WantedBy=multi-user.target
```

## Dashboard service

```ini
[Unit]
Description=Soniq Dashboard
After=network.target

[Service]
Type=exec
User=soniq
Group=soniq
WorkingDirectory=/opt/soniq
Environment=SONIQ_DATABASE_URL=postgresql://soniq:password@localhost/soniq_prod
ExecStart=/opt/soniq/venv/bin/soniq dashboard --host=127.0.0.1 --port=6161
Restart=always
RestartSec=5

NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/opt/soniq /var/log/soniq
MemoryMax=256M

StandardOutput=journal
StandardError=journal
SyslogIdentifier=soniq-dashboard

[Install]
WantedBy=multi-user.target
```

## Scheduler service

If you use `@soniq.periodic`, run `soniq scheduler` as its own service. Copy `deployment/soniq-scheduler.service` and set the same `SONIQ_DATABASE_URL` and `SONIQ_JOBS_MODULES` as the worker. Without it, recurring jobs never fire.

The dashboard example binds to `127.0.0.1`, so put a reverse proxy in front of it. To bind to `0.0.0.0`, also set `SONIQ_DASHBOARD_API_KEY`. Without a key, the dashboard rejects requests whose Host header is not `localhost`.

## Manage the services

```bash
sudo cp deployment/soniq-worker.service /etc/systemd/system/
sudo cp deployment/soniq-scheduler.service /etc/systemd/system/
sudo cp deployment/soniq-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload

sudo systemctl enable soniq-worker soniq-scheduler soniq-dashboard
sudo systemctl start soniq-worker soniq-scheduler soniq-dashboard

# Check status
sudo systemctl status soniq-worker

# View logs
sudo journalctl -u soniq-worker -f

# Restart
sudo systemctl restart soniq-worker
```

## See also

- [Deployment overview](deployment.md) - prerequisites, queue routing, performance tuning
- [Reliability](reliability.md) - graceful shutdown, stuck-job recovery
