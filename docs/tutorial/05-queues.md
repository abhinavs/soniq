---
description: Route Soniq background jobs to named queues and priorities, and run dedicated worker pools per queue with soniq worker --queues. Includes queue stats.
---

# 5. Route jobs with named queues and priorities

> **Intermediate** - 6 minutes. Routing jobs to dedicated worker pools.

A Soniq queue is a named group of jobs. Use queues to separate jobs by workload type and to choose which workers run them. Jobs go to the `default` queue unless you name another.

## Named queues

Assign a job to a queue in the decorator:

```python
@soniq.job(name="emails.send_welcome_email", queue="emails")
async def send_welcome_email(user_id: int):
    ...

@soniq.job(name="billing.charge_subscription", queue="billing")
async def charge_subscription(account_id: str, amount: int):
    ...

@soniq.job(name="images.process_thumbnail")  # defaults to "default" queue
async def process_thumbnail(image_id: str):
    ...
```

You can also override the queue at enqueue time:

```python
await soniq.enqueue(send_welcome_email, user_id=42, queue="urgent")
```

## Priority ordering

Within a queue, workers claim jobs by priority. A lower number means a higher priority. Jobs with the same priority run in the order that they became ready (first in, first out).

| Priority | Typical use |
| --- | --- |
| 1 | Urgent - user-facing, time-sensitive |
| 10 | High - important but not blocking |
| 100 | Default - the `@soniq.job()` default |

```python
@soniq.job(name="billing.charge_subscription", queue="billing", priority=10)
async def charge_subscription(account_id: str, amount: int):
    ...

# One-off priority override
await soniq.enqueue(charge_subscription, account_id="acct_123", amount=999, priority=1)
```

## Running workers on specific queues

By default, a worker processes all queues. If you run `soniq worker` with no `--queues` flag, the worker claims jobs from every queue in your database.

To limit a worker to some queues, pass `--queues`:

```bash
# Process only email and billing jobs
soniq worker --queues emails,billing

# Dedicated urgent worker with higher concurrency
soniq worker --queues urgent --concurrency 8
```

To run one worker process for each queue group, start two workers:

```bash
soniq worker --queues emails,billing --concurrency 4
soniq worker --queues urgent --concurrency 8
```

Now you can scale each queue on its own. For example, run more email workers at peak hours, or give your billing queue a fast machine.

## Queue stats

```python
stats = await soniq.get_queue_stats()
print(stats)  # {'total': ..., 'queued': ..., 'processing': ..., 'done': ..., 'dead_letter': ..., 'cancelled': ...}
```

`get_queue_stats()` returns job counts for the whole instance, not one count per queue. To list the jobs in one queue, use `soniq.list_jobs(queue="billing", status="queued")`.

From the CLI:

```bash
soniq status
```

## Design advice

**Split by workload type.** Keep CPU-bound image processing apart from fast email sends. Then slow jobs cannot block quick jobs.

**Keep payloads small.** Pass IDs, not large data. Do not enqueue a 10 MB CSV. Store the file elsewhere and pass its storage key. Soniq stores job arguments as JSON in the database.

**Run a worker group for each queue.** A worker group is a set of processes that use the same `--queues` flag. Each group scales on its own and fails on its own. A crash in image processing does not affect your email workers.

## Next steps

Continue with [chapter 6: transactional enqueue](06-transactional-enqueue.md).
