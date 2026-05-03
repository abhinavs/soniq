# Cross-service task stubs

This recipe shows how to share typed task references between two services
without either side importing the other's implementation. The producer
gets compile-time argument validation, the consumer keeps full control
of the handler, and the wire is just a task name plus a JSON args dict.

The pattern: a tiny stub package, importable by both services, that
contains nothing but `task_ref(...)` declarations.

## Why a stub package

Once a job crosses repo boundaries, two questions become awkward:

1. **The producer cannot import the consumer's handler.** The consumer
   service owns the implementation; pulling its codebase into every
   producer is an architectural mistake.
2. **The producer still wants typed args.** Without it, a producer
   typo like `args={"order_id": 123}` (int instead of str) only fails
   when the consumer dead-letters the row hours later.

A stub package solves both: a tiny, dependency-light package that
declares the task name, the Pydantic args model, and (optionally) a
default queue. Producers `pip install` it; consumers `pip install` it
too and register their handler against the same `name`.

## The stub package

```
myservice-tasks/
├── pyproject.toml
└── myservice_tasks/
    ├── __init__.py
    ├── billing.py
    └── schemas.py
```

```python
# myservice_tasks/schemas.py
from pydantic import BaseModel

class InvoiceArgs(BaseModel):
    order_id: str
    customer: str
```

```python
# myservice_tasks/billing.py
from soniq import task_ref
from .schemas import InvoiceArgs

send_invoice = task_ref(
    name="billing.invoices.send.v2",
    args_model=InvoiceArgs,
    default_queue="billing",
)
```

```python
# myservice_tasks/__init__.py
from .billing import send_invoice

__all__ = ["send_invoice"]
```

That's the whole package. No async runtime, no Soniq instance, no
network calls. The `task_ref(...)` factory validates the name against
`SONIQ_TASK_NAME_PATTERN` at import time so a typo fails at the stub
declaration rather than as a dead-letter row in production.

## The producer

```python
# producer service A
import asyncio
from soniq import Soniq
from myservice_tasks import send_invoice

producer = Soniq(database_url="postgresql://shared-pg/jobs")

async def main():
    await producer.enqueue(
        send_invoice,
        args={"order_id": "o1", "customer": "acme"},
    )

asyncio.run(main())
```

The producer:

- Imports `send_invoice` from the stub package - a tiny `TaskRef`
  value, not a callable.
- Calls `producer.enqueue(send_invoice, args={...})`. The TaskRef arm
  validates `args` against `InvoiceArgs` before writing the row.
  If a producer typo passes `order_id=123` (int), the call raises
  `SoniqError(SONIQ_TASK_ARGS_INVALID)` immediately.
- Never calls `@app.job` or `run_worker`. Producer-vs-consumer is a
  deployment convention; see [Deployment shapes](../production/deployment-shapes.md).

## The consumer

```python
# consumer service B
from soniq import Soniq
from myservice_tasks import send_invoice  # same stub
from myservice_tasks.schemas import InvoiceArgs

consumer = Soniq(database_url="postgresql://shared-pg/jobs")

@consumer.job(name=send_invoice.name, validate=InvoiceArgs)
async def send_invoice_handler(order_id: str, customer: str):
    # ... actually send the invoice ...
    pass

if __name__ == "__main__":
    import asyncio
    asyncio.run(consumer.run_worker())
```

The consumer:

- Imports the same stub. `send_invoice.name` is the canonical wire
  identifier; the consumer's handler registers under that name so the
  producer's enqueue lands at this handler.
- Re-applies `validate=InvoiceArgs` on the handler. The consumer's
  validation is the second line of defence (the producer can be older
  or run with weakened validation).

## Why this works

- **No implementation crosses the wire.** The stub package contains
  schemas and names. The producer never imports the consumer's
  handler module; the consumer never imports the producer's app.
- **Typed at the call site.** The producer's IDE knows
  `args=InvoiceArgs(...)` is what `send_invoice` expects, because the
  TaskRef carries `args_model=InvoiceArgs`.
- **Renames are cheap.** Renaming the consumer's handler does not
  break producers, because the wire identifier is `send_invoice.name`,
  not the Python name.
- **Versioning is explicit.** Bumping to `billing.invoices.send.v3`
  is a stub-package version bump and a coordinated deploy, not a
  silent runtime break.

## Versioning convention

Stub packages should use a dotted suffix in the task name so two
shapes can coexist:

```python
# good
send_invoice_v2 = task_ref(name="billing.invoices.send.v2", ...)

# avoid - same name, different shape
# (consumers can't tell which version a row belongs to)
```

When the args shape changes, ship `...send.v3` alongside `...send.v2`
for a release window. In-flight rows with the v2 name continue to
dispatch at the v2 handler; new producers move to v3.

## Drift detection

The dead-letter queue is the operational signal of drift: rows with
reason `Job <name> not registered` flag a producer using a name that
no consumer has registered.
