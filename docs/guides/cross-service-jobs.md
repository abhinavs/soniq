# Cross-service jobs

Soniq enqueues by **task name**, not by Python function reference. That
means service A can enqueue a job that service B owns and executes,
provided both share a Postgres database. Neither service needs to
import the other's code.

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

Two repositories, one shared Postgres, one task name. The producer
writes the row; the consumer's worker picks it up because both agree
on the name.

## Validation modes

Producers that have no local registry should set
`SONIQ_ENQUEUE_VALIDATION` explicitly. The setting governs what
`enqueue()` does when the name is not registered locally.

| Mode | Behaviour | When to use |
| --- | --- | --- |
| `strict` (default) | Raise `SONIQ_UNKNOWN_TASK_NAME`. No row written. | Single-repo deployments where every name is registered locally. |
| `warn` | Rate-limited WARN per unknown name; row is written. | Producer services that occasionally enqueue cross-service. |
| `none` | Silent passthrough. | Pure-producer services with no local registry by design. |

The default is `strict` because typos in async systems surface hours
later in dead-letter queues. Producers that genuinely cannot validate
locally make that explicit by setting `=warn` or `=none` in their
deployment environment.

## Naming conventions

Names are protocol identifiers. By default `@app.job` derives a name
from `f"{module}.{qualname}"` (Celery-style) - that's fine for
single-repo usage. Cross-service deployments should pass `name=`
explicitly so the wire identifier doesn't rot when functions get
renamed:

```
billing.invoices.send.v2
```

The recommended format is dotted lowercase with a version suffix.
The default pattern (`SONIQ_TASK_NAME_PATTERN`) enforces ASCII,
dot-separated, lowercase, no whitespace, no leading or trailing
dots when an explicit `name=` is passed. Module-derived names skip
pattern validation since the user did not pick them. Explicit names
that violate the pattern raise `SONIQ_INVALID_TASK_NAME` at
registration time.

## Failure semantics

- **Unknown name in strict mode:** producer raises before writing.
- **Unknown name in warn/none mode:** row is written; the consumer
  worker receives it, can't find the registered handler, and
  dead-letters it with `Job <name> not registered.` as the error
  reason. Other queue work is unaffected.
- **Args fail consumer's `args_model`:** dead-letter with the
  validation error. Producer sees nothing - validation is consumer-side
  unless the producer also has a local model (typically via a shared
  stub package).
- **Consumer registers the name later:** rows queued before
  registration are picked up normally once the consumer comes online.

## At-least-once delivery and idempotency

Soniq is **at-least-once**. A successful `enqueue` means the row is
durably committed; it does not mean the handler ran exactly once.
The handler can run more than once because of:

- Producer retries after a network blip
- Worker crashes mid-execution (the row is requeued)
- Manual replay via `app.dead_letter.replay(...)` or the dashboard

**Handlers must be idempotent.** The recommended pattern is an
application-level idempotency key threaded through `dedup_key`:

```python
@app.job(name="billing.invoices.send.v2", validate=InvoiceArgs)
async def send_invoice(order_id: str, customer: str):
    if await invoice_already_sent(order_id):
        return  # idempotent short-circuit
    await send(order_id, customer)
    await mark_invoice_sent(order_id)
```

`dedup_key` and `unique=True` are operational helpers, not delivery
guarantees. They reduce duplicate work; they do not eliminate it.

## Producer / consumer split

Producer-vs-consumer is a deployment convention, not a class. The same
`Soniq` runs in both roles; what differs is whether the deployment
imports handler modules and runs `run_worker(...)`. See
[Deployment shapes](../production/deployment-shapes.md) for the
producer service, consumer service, and shared-library patterns.
