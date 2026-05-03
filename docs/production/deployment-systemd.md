# Systemd

Best for modern Linux servers with direct process control. Files: `deployment/soniq-worker.service`, `deployment/soniq-scheduler.service`, and `deployment/soniq-dashboard.service`.

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
ExecReload=/bin/kill -HUP $MAINPID
KillMode=mixed
Restart=always
RestartSec=5
StartLimitIntervalSec=0

# Security hardening
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/opt/soniq /var/log/soniq

# Resource limits
MemoryMax=512M
CPUQuota=200%

# Graceful shutdown -- match your longest job timeout
TimeoutStopSec=310

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
After=network.target soniq-worker.service
Wants=soniq-worker.service

[Service]
Type=exec
User=soniq
Group=soniq
WorkingDirectory=/opt/soniq
Environment=SONIQ_DATABASE_URL=postgresql://soniq:password@localhost/soniq_prod
ExecStart=/opt/soniq/venv/bin/soniq dashboard --host=0.0.0.0 --port=8000
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

## Managing the services

```bash
sudo cp deployment/soniq-worker.service /etc/systemd/system/
sudo cp deployment/soniq-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload

sudo systemctl enable soniq-worker soniq-dashboard
sudo systemctl start soniq-worker soniq-dashboard

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
