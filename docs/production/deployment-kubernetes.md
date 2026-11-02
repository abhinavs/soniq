---
description: Deploy Soniq workers, the scheduler and the dashboard on Kubernetes with PostgreSQL. Secrets, Deployments, graceful shutdown, autoscaling and a setup Job.
---

# Run Soniq on Kubernetes

Kubernetes suits container platforms with autoscaling. The repo has the full manifest at `deployment/kubernetes.yaml`. The examples below are condensed. The `registry.example.com/...` images are placeholders. Build your own from `deployment/Dockerfile.worker` and `deployment/Dockerfile.dashboard`, with your job code baked in.

## Secret and ConfigMap

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: soniq-secrets
  namespace: soniq
type: Opaque
stringData:
  SONIQ_DATABASE_URL: "postgresql://user:pass@host:5432/db"
  SONIQ_DASHBOARD_API_KEY: "a-long-random-string"

---
apiVersion: v1
kind: ConfigMap
metadata:
  name: soniq-config
  namespace: soniq
data:
  SONIQ_LOG_LEVEL: "INFO"
  SONIQ_JOBS_MODULES: "myapp.jobs"
```

## Worker Deployment

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: soniq-worker
  namespace: soniq
spec:
  replicas: 3
  selector:
    matchLabels:
      app: soniq-worker
  template:
    metadata:
      labels:
        app: soniq-worker
    spec:
      terminationGracePeriodSeconds: 45  # longer than SONIQ_SHUTDOWN_TIMEOUT (30s)
      containers:
      - name: worker
        image: registry.example.com/myapp-worker:1.0.0
        args: ["soniq", "worker", "--concurrency=4"]
        env:
        - name: SONIQ_DATABASE_URL
          valueFrom:
            secretKeyRef:
              name: soniq-secrets
              key: SONIQ_DATABASE_URL
        # The scheduler Deployment runs @periodic jobs.
        - name: SONIQ_SCHEDULER_SUPPRESS_WARNING
          value: "1"
        envFrom:
        - configMapRef:
            name: soniq-config
        resources:
          requests:
            memory: "256Mi"
            cpu: "250m"
          limits:
            memory: "512Mi"
            cpu: "500m"
```

The `soniq` CLI has no `health` or `ready` command, so this example has no exec probes on the worker. Kubernetes restarts the container if the process exits. To check worker health from outside, run `soniq inspect` or watch the dashboard.

## Scheduler Deployment

The scheduler is a separate Deployment. Its replicas elect one leader through a PostgreSQL advisory lock, and the others wait. One replica is enough. Run two or three if you want failover.

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: soniq-scheduler
  namespace: soniq
spec:
  replicas: 1
  selector:
    matchLabels:
      app: soniq-scheduler
  template:
    metadata:
      labels:
        app: soniq-scheduler
    spec:
      containers:
      - name: scheduler
        image: registry.example.com/myapp-worker:1.0.0
        args: ["soniq", "scheduler"]
        env:
        - name: SONIQ_DATABASE_URL
          valueFrom:
            secretKeyRef:
              name: soniq-secrets
              key: SONIQ_DATABASE_URL
        envFrom:
        - configMapRef:
            name: soniq-config
```

## Dashboard Deployment + Service

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: soniq-dashboard
  namespace: soniq
spec:
  replicas: 2
  selector:
    matchLabels:
      app: soniq-dashboard
  template:
    metadata:
      labels:
        app: soniq-dashboard
    spec:
      containers:
      - name: dashboard
        image: registry.example.com/myapp-dashboard:1.0.0
        args: ["soniq", "dashboard", "--host=0.0.0.0", "--port=6161"]
        env:
        - name: SONIQ_DATABASE_URL
          valueFrom:
            secretKeyRef:
              name: soniq-secrets
              key: SONIQ_DATABASE_URL
        - name: SONIQ_DASHBOARD_API_KEY
          valueFrom:
            secretKeyRef:
              name: soniq-secrets
              key: SONIQ_DASHBOARD_API_KEY
        envFrom:
        - configMapRef:
            name: soniq-config
        ports:
        - containerPort: 6161
        livenessProbe:
          httpGet:
            path: /
            port: 6161
          periodSeconds: 30
        readinessProbe:
          httpGet:
            path: /
            port: 6161
          periodSeconds: 10

---
apiVersion: v1
kind: Service
metadata:
  name: soniq-dashboard
  namespace: soniq
spec:
  selector:
    app: soniq-dashboard
  ports:
  - port: 80
    targetPort: 6161
  type: ClusterIP
```

The dashboard needs `SONIQ_DASHBOARD_API_KEY` on Kubernetes. Without a key, it rejects any Host header that is not `localhost`, so probes to the pod IP and requests through the Ingress fail with HTTP 400. With a key, every path except `/` needs the `X-API-Key` header. For this reason, the probes use `/` and not `/api/system/health`.

## Autoscaling

```bash
kubectl autoscale deployment soniq-worker \
  --namespace=soniq \
  --cpu-percent=70 \
  --min=2 --max=10
```

The `deployment/kubernetes.yaml` file also includes a HorizontalPodAutoscaler manifest. The dashboard has no `/metrics` endpoint. It serves JSON metrics at `/api/metrics`, not the Prometheus text format. For Prometheus scraping, expose `PrometheusMetricsSink` from your own app, as described in [going to production](going-to-production.md#logging-and-observability).

## Migration job

Run `soniq setup` once per deploy, not from every replica. Roll out the setup `Job` before you roll out the worker Deployment:

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: soniq-setup
  namespace: soniq
spec:
  template:
    spec:
      restartPolicy: OnFailure
      containers:
      - name: setup
        image: registry.example.com/myapp-worker:1.0.0
        args: ["soniq", "setup"]
        env:
        - name: SONIQ_DATABASE_URL
          valueFrom:
            secretKeyRef:
              name: soniq-secrets
              key: SONIQ_DATABASE_URL
```

## See also

- [Deployment overview](deployment.md) - prerequisites, queue routing, performance tuning
- [Reliability](reliability.md) - graceful shutdown, stuck-job recovery
- [PostgreSQL tuning](postgres.md) - connection pool sizing under high replica counts
