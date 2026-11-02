---
description: Retry failed Python background jobs in Soniq with fixed delays, per-attempt delays, exponential backoff and jitter. Includes dead-letter queue and idempotency.
---

# 3. Retry failed jobs with backoff

> **Beginner** - 7 minutes. Retry counts, delays, backoff, and the dead-letter queue.

Soniq retries a failed job automatically. A job has 3 retries by default, which makes 4 attempts in total. Set the retry count, the delay, and the backoff in the `@soniq.job()` decorator.

> **Terms.** Soniq guarantees *at-least-once* delivery. A job runs at least once and can run more than once if a worker crashes during the handler. *Idempotent* means safe to run more than once with the same end result. Retries are the most common reason a job runs twice, so handlers must be idempotent. The [Idempotency](#idempotency) section shows how.

## Configuration

```python
@soniq.job(
    name="payments.call_payment_api",
    retries=5,        # retry up to 5 times (6 total attempts)
    retry_delay=10,       # wait 10 seconds between retries
    retry_backoff=True,   # exponential backoff (with jitter)
    retry_max_delay=300,  # never wait more than 5 minutes
)
async def call_payment_api(invoice_id: str):
    ...
```

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `retries` | `int` | `3` | Number of retries after the first failure. `max_retries` is an alias. |
| `retry_delay` | `int \| list[int]` | `0` | Seconds between retries |
| `retry_backoff` | `bool` | `False` | Double the delay on each attempt |
| `retry_max_delay` | `int \| None` | `None` | Upper limit for the computed delay, in seconds |
| `retry_jitter` | `bool` | `True` | With `retry_backoff`, wait a random time between half the delay and the full delay |

## Fixed delay

Use one wait time for every retry:

```python
@soniq.job(name="inventory.sync_inventory", retries=5, retry_delay=10)
async def sync_inventory(product_id: str):
    ...
```

Retries at: 10s, 10s, 10s, 10s, 10s.

## Per-attempt delays

Pass a list to set the delay for each retry:

```python
@soniq.job(name="emails.send_verification_email", retries=3, retry_delay=[1, 5, 30])
async def send_verification_email(user_id: int):
    ...
```

Retries at: 1s, 5s, 30s. If a job has more retries than list entries, Soniq reuses the last value.

## Exponential backoff

Set `retry_backoff=True` to double the delay on each attempt:

```python
@soniq.job(name="payments.call_payment_api", retries=5, retry_delay=10, retry_backoff=True)
async def call_payment_api(invoice_id: str):
    ...
```

Retry delays before jitter: 10s, 20s, 40s, 80s, 160s.

If `retry_delay` is `0`, Soniq uses a base of 1 second: 1s, 2s, 4s, 8s, 16s.

**Jitter.** By default, backoff delays include jitter. Soniq waits a random time between half of the computed delay and the full delay. Jitter stops many failed jobs from retrying at the same moment. Set `retry_jitter=False` for the exact delays above. Jitter does not apply to fixed delays or delay lists.

## Max delay cap

Set `retry_max_delay` to stop the delay from growing without limit:

```python
@soniq.job(name="reports.fetch_external_report", retries=8, retry_delay=5, retry_backoff=True, retry_max_delay=300)
async def fetch_external_report(report_id: str):
    ...
```

Retry delays before jitter: 5s, 10s, 20s, 40s, 80s, 160s, 300s, 300s. The delay never exceeds `retry_max_delay`.

## After max retries: the dead-letter queue

When a job uses all its retries, Soniq moves it from `soniq_jobs` to the *dead-letter queue*. This is the `soniq_dead_letter_jobs` table. Soniq deletes the original `soniq_jobs` row in the same transaction.

Dead-letter jobs do not run on their own. Inspect them, fix the cause, and replay the jobs that you want to run again. See [Dead-letter queue](../reference/dead-letter.md) for the inspect and replay steps.

## Examples

### API call with backoff

External APIs can rate-limit requests or have short outages. Exponential backoff gives them time to recover.

```python
@soniq.job(name="users.sync_user_to_crm", retries=5, retry_delay=2, retry_backoff=True, retry_max_delay=120)
async def sync_user_to_crm(user_id: int):
    user = await get_user(user_id)
    async with httpx.AsyncClient() as client:
        resp = await client.post("https://crm.example.com/api/users", json=user.dict())
        resp.raise_for_status()
```

### Email sending with fixed delay

Email servers sometimes refuse connections for a short time. A short fixed delay is usually enough.

```python
@soniq.job(name="orders.send_order_confirmation", retries=3, retry_delay=5)
async def send_order_confirmation(order_id: str):
    order = await get_order(order_id)
    await smtp_send(to=order.email, subject=f"Order {order_id} confirmed", body=...)
```

### File processing with escalating delays

File imports can fail because of locks or short storage problems. Increase the delay on each retry to reduce load on the system.

```python
@soniq.job(name="files.import_csv", retries=3, retry_delay=[5, 30, 120])
async def import_csv(file_path: str):
    async with aiofiles.open(file_path) as f:
        rows = await parse_csv(f)
    await bulk_insert(rows)
```

## Idempotency

Soniq provides *at-least-once* delivery. A job runs at least once and can run more than once. Retries cause repeats. A worker crash during a handler also causes a repeat, because another worker runs the job again. Every job queue that does not lose work has this property.

To handle repeats, make your handler *idempotent*. Use one of these methods:

- Use `INSERT ... ON CONFLICT DO UPDATE` instead of a plain insert. The second run updates the existing row and does not create a duplicate.
- Store an idempotency key, such as a hash of the job arguments or a unique field on the request. Check the key before the side effect. If the key shows the work is done, return.
- Check the current state before you act. Before you send an email, check whether you recorded it as sent. Before you charge a card, check whether the charge exists in your payments table.

Make the second run do nothing. Do not try to prevent the second run.

## Job timeouts

Every job has a default timeout of 300 seconds (5 minutes). A job that exceeds its timeout counts as a failure and follows the normal retry rules.

```python
@soniq.job(name="jobs.quick_health_check", retries=3, timeout=60)
async def quick_health_check(service_url: str):
    ...

@soniq.job(name="jobs.long_running_migration", timeout=0)  # no timeout for this job
async def long_running_migration():
    ...
```

`timeout=None` (the default) uses the global setting. Change the global default with `SONIQ_JOB_TIMEOUT`. Set it to `0` to disable timeouts globally.

## Next steps

Continue with [chapter 4: scheduling](04-scheduling.md).
