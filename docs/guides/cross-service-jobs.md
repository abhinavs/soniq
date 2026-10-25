---
description: Enqueue a Soniq job by name from one Python service and run it in another, with a shared PostgreSQL database. Covers validation modes, naming and failures.
---

# Cross-service jobs: enqueue by name between Python services

Soniq enqueues by job name, not by Python function reference. Service A can enqueue a job that service B runs, if both services use the same PostgreSQL database. Neither service imports the other's code.

## The minimal shape

**Producer (service A)** writes by name:

```python
from soniq import Soniq

producer = Soniq(
    database_url="postgresql://shared-pg/jobs",
    enqueue_validation="none",  # producer has no local registry
)

await producer.enqueue(
    "billing.invoices.send.v2",
    args={"order_id": "o1", "customer": "acme"},
)
```

**Consumer (service B)** registers the handler:

```python
# myservice/tasks.py
from soniq import Soniq
from pydantic import BaseModel

consumer = Soniq(database_url="postgresql://shared-pg/jobs")

class InvoiceArgs(BaseModel):
    order_id: str
    customer: str

@consumer.job(name="billing.invoices.send.v2", validate=InvoiceArgs)
async def send_invoice(order_id: str, customer: str):
    ...
```

And starts the worker with the CLI:

```bash
export SONIQ_DATABASE_URL="postgresql://shared-pg/jobs"
export SONIQ_JOBS_MODULES="myservice.tasks"
soniq worker
```

This setup has two repositories, one shared PostgreSQL database and one job name. The producer writes the row. The consumer's worker claims the row because both services use the same name.

## Validation modes

A producer with no local registry must set `enqueue_validation` (or `SONIQ_ENQUEUE_VALIDATION`) explicitly. The setting controls what `enqueue()` does when the name is not registered locally.

| Mode | Behaviour | When to use |
| --- | --- | --- |
| `strict` (default) | Raise `SONIQ_UNKNOWN_TASK_NAME`. No row written. | Single-repo deployments where every name is registered locally. |
| `warn` | Logs a rate-limited warning per unknown name and writes the row. | Producer services that occasionally enqueue cross-service. |
| `none` | Writes the row and logs nothing. | Pure-producer services with no local registry by design. |

The default is `strict` because a typo in a job name otherwise appears hours later in the dead-letter queue. A producer that cannot validate locally must set `warn` or `none` in its environment.

## Naming conventions

A job name is the contract between the producer and the consumer. Always pass `name=` to `@soniq.job()`. A name derived from the module path is deprecated and changes when you rename the function. Use dotted lowercase names with a version suffix:

```
billing.invoices.send.v2
```

The default pattern is `^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$`. You can change it with `SONIQ_TASK_NAME_PATTERN`. A name that does not match the pattern raises `SONIQ_INVALID_TASK_NAME` at registration. `enqueue()` checks string names against the same pattern.

## Failure semantics

- **Unknown name in strict mode:** the producer raises before it writes a row.
- **Unknown name in warn or none mode:** the producer writes the row. The consumer worker cannot find a handler and dead-letters the job with the error `Job <name> not registered.` Other jobs in the queue are not affected.
- **Args fail the consumer's `args_model`:** the consumer dead-letters the job with the validation error. The producer sees no error, unless it also has a local model, for example from a shared stub package.
- **Consumer registers the name later:** the consumer runs jobs that were queued before it registered the name, after it starts.

## At-least-once delivery and idempotency

Soniq delivers jobs **at least once**. A successful `enqueue()` means the row is committed. It does not mean the handler runs exactly once. A handler can run more than once because of:

- Producer retries after a network error
- Worker crashes during a job (Soniq puts the job back in the queue)
- Manual replay with `soniq.dead_letter.replay(...)` or the dashboard

**Handlers must be idempotent.** Check in the handler whether the work is already done:

```python
@soniq.job(name="billing.invoices.send.v2", validate=InvoiceArgs)
async def send_invoice(order_id: str, customer: str):
    if await invoice_already_sent(order_id):
        return  # idempotent short-circuit
    await send(order_id, customer)
    await mark_invoice_sent(order_id)
```

`dedup_key` and `unique=True` reduce duplicate jobs, but they are not delivery guarantees. They compare against jobs that are still queued only.

## Producer / consumer split

Producer and consumer are deployment roles, not classes. The same `Soniq` class serves both roles. A consumer imports the handler modules and runs a worker. See [Deployment shapes](../production/deployment-shapes.md) for the producer service, consumer service and shared-library patterns.

## Next steps

- [Cross-service task stubs](../recipes/cross-service-task-stubs.md) - validate producer arguments with `TaskRef`
- [Jobs API reference](../api/jobs.md) - `enqueue()` options and input shapes
