# Deployment shapes

Soniq is one class for both producers and consumers. Producer-vs-consumer
is a deployment convention, not a class hierarchy or a constructor flag.
The same `Soniq` runs in both roles; what differs is what the deployment
imports and what it executes.

## Producer service

A producer service connects to the queue, enqueues jobs by name, and
never starts a worker. It does not need to import the handler modules:
the canonical task name is the contract.

```python
from soniq import Soniq

app = Soniq(database_url="postgresql://...")

await app.enqueue("billing.send", args={"order_id": 7})
```

If a producer accidentally calls `run_worker(...)`, it polls the queue
with zero registered handlers and dead-letters jobs by `Job not
registered`. That's a fast, actionable failure - the misconfiguration
shows up in the worker's first poll, not at 3am.

## Consumer service

A consumer service registers handlers with `@app.job` and runs a worker:

```python
from soniq import Soniq

app = Soniq(database_url="postgresql://...")

@app.job(name="billing.send")
async def send(order_id: int): ...

await app.run_worker()
```

The same instance can also enqueue (the worker process itself often
re-enqueues follow-up jobs). The "consumer" label is just shorthand for
"this deployment runs a worker."

## Shared-library shape (recommended cross-service)

When the producer and the consumer live in different repos or services,
share a small stub package that declares the canonical task name and an
args model. Both sides import it; neither side needs to know about the
other's internals.

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

await app.enqueue(SEND_INVOICE, args=SendInvoiceArgs(
    order_id="o1", customer="acme"
).model_dump())
```

Consumer service:

```python
from billing_tasks import SEND_INVOICE, SendInvoiceArgs

@app.job(name=SEND_INVOICE.name, validate=SendInvoiceArgs)
async def send_invoice(order_id: str, customer: str): ...

await app.run_worker(queues=["billing"])
```

The `TaskRef` carries the canonical name, the args model, and the
default queue across the wire. The producer gets validation at enqueue
time; the consumer gets validation again at dispatch time. No shared
runtime, no class hierarchy.
