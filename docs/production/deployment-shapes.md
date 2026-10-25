---
description: Run Soniq as a producer service, a consumer service, or both. Share a TaskRef stub package so Python services enqueue and run jobs by canonical name.
---

# Producer and consumer deployment shapes

Soniq uses one class, `Soniq`, for both producers and consumers. The producer-or-consumer split is a deployment convention, not a class hierarchy or a constructor flag. The same `Soniq` runs in both roles. What differs is what the deployment imports and what it executes.

## Producer service

A producer service connects to the queue, enqueues jobs by name, and never starts a worker. It does not need to import the handler modules. The canonical job name is the contract.

```python
from soniq import Soniq

soniq = Soniq(database_url="postgresql://...", enqueue_validation="none")

await soniq.enqueue("billing.send", args={"order_id": 7})
```

The default `enqueue_validation="strict"` raises `SONIQ_UNKNOWN_TASK_NAME` for a name that is not registered in the producer process. A pure producer sets `"none"` (or `"warn"`), or enqueues a `TaskRef` as shown below.

If a producer calls `run_worker(...)` by mistake, it polls the queue with no registered handlers. It moves each job it claims to the dead-letter queue with the reason `job_not_found`. The misconfiguration shows up on the first poll.

## Consumer service

A consumer service registers handlers with `@soniq.job()` and runs a worker:

```python
from soniq import Soniq

soniq = Soniq(database_url="postgresql://...")

@soniq.job(name="billing.send")
async def send(order_id: int): ...

await soniq.run_worker()
```

The same instance can also enqueue. The worker process often enqueues follow-up jobs. The "consumer" label means "this deployment runs a worker".

## Share a task stub between services

When the producer and the consumer live in different repositories, share a small stub package. It declares the canonical job name and an args model. Both sides import it, and neither side needs the internals of the other.

```python
# billing_tasks/__init__.py - imported by both producer and consumer
from soniq import task_ref
from pydantic import BaseModel

class SendInvoiceArgs(BaseModel):
    order_id: str
    customer: str

SEND_INVOICE = task_ref(
    name="billing.send_invoice",
    args_model=SendInvoiceArgs,
    default_queue="billing",
)
```

Producer service:

```python
from billing_tasks import SEND_INVOICE, SendInvoiceArgs

await soniq.enqueue(SEND_INVOICE, args=SendInvoiceArgs(
    order_id="o1", customer="acme"
).model_dump())
```

Consumer service:

```python
from billing_tasks import SEND_INVOICE, SendInvoiceArgs

@soniq.job(name=SEND_INVOICE.name, validate=SendInvoiceArgs)
async def send_invoice(order_id: str, customer: str): ...

await soniq.run_worker(queues=["billing"])
```

The `TaskRef` carries the canonical name, the args model and the default queue between the services. The producer gets validation at enqueue time. The consumer gets validation again at dispatch time. The services share no runtime and no class hierarchy.

## Next steps

- [Cross-service jobs](../guides/cross-service-jobs.md) - the full producer and consumer guide
- [Deployment](deployment.md) - systemd, Docker Compose, Kubernetes and Supervisor recipes
