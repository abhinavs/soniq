# Kubernetes

Best for containerized environments with autoscaling. File: `deployment/kubernetes.yaml`.

## Secret and ConfigMap

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: soniq-secrets
  namespace: soniq
type: Opaque
data:
  # echo -n "postgresql://user:pass@host/db" | base64
  SONIQ_DATABASE_URL: <base64-encoded-url>

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
      terminationGracePeriodSeconds: 310  # match your longest job timeout
      containers:
      - name: worker
        image: soniq/worker:latest
        args: ["soniq", "worker", "--concurrency=4"]
        env:
        - name: SONIQ_DATABASE_URL
          valueFrom:
            secretKeyRef:
              name: soniq-secrets
              key: SONIQ_DATABASE_URL
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
        livenessProbe:
          exec:
            command: ["soniq", "health"]
          initialDelaySeconds: 30
          periodSeconds: 30
        readinessProbe:
          exec:
            command: ["soniq", "ready"]
          initialDelaySeconds: 5
          periodSeconds: 10
```

## Scheduler Deployment

The scheduler is a separate Deployment. Multiple replicas coordinate via a Postgres advisory lock - one is leader, the rest idle. One replica is enough; two or three is fine if you want failover.

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
        image: soniq/worker:latest
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
        image: soniq/dashboard:latest
        args: ["soniq", "dashboard", "--host=0.0.0.0", "--port=8000"]
        env:
        - name: SONIQ_DATABASE_URL
          valueFrom:
            secretKeyRef:
              name: soniq-secrets
              key: SONIQ_DATABASE_URL
        envFrom:
        - configMapRef:
            name: soniq-config
        ports:
        - containerPort: 8000
        livenessProbe:
          httpGet:
            path: /health
            port: 8000
          periodSeconds: 30
        readinessProbe:
          httpGet:
            path: /ready
            port: 8000
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
    targetPort: 8000
  type: ClusterIP
```

## Autoscaling

```bash
kubectl autoscale deployment soniq-worker \
  --namespace=soniq \
  --cpu-percent=70 \
  --min=2 --max=10
```

The `deployment/kubernetes.yaml` file also includes an HPA manifest and a ServiceMonitor for Prometheus.

## Migration job

`soniq setup` should run once per deploy, not from every replica. Run it as a `Job` (or an `initContainer` on a single-replica gate Deployment) before the worker Deployment is rolled out:

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
        image: soniq/worker:latest
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
