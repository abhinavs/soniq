# 3. Retries

> **Beginner** - 7 minutes. Retry counts, delays, backoff, and the dead-letter queue.

Soniq retries failed jobs automatically. Every job gets 3 retries by default (4 total attempts). You control the retry count, delay strategy, and backoff at the decorator level.

> **A note on terms.** Soniq guarantees *at-least-once* delivery: a job will run at least once, and may run more than once if a worker crashes mid-handler. *Idempotent* means "safe to run more than once with the same end result." The two ideas come together in this chapter - retries are the most common reason a job runs twice, so handlers need to be idempotent. The [Idempotency](#idempotency) section at the bottom shows how.

## Configuration

```python
@app.job(
    max_retries=5,        # retry up to 5 times (6 total attempts)
    retry_delay=10,       # wait 10 seconds between retries
    retry_backoff=True,   # exponential backoff
    retry_max_delay=300,  # never wait more than 5 minutes
)
async def call_payment_api(invoice_id: str):
    ...
```

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `max_retries` | `int` | `3` | Number of retry attempts after first failure |
| `retry_delay` | `int \| list[int]` | `0` | Seconds between retries |
| `retry_backoff` | `bool` | `False` | Multiply delay exponentially on each attempt |
| `retry_max_delay` | `int \| None` | `None` | Upper bound on computed delay |

## Fixed delay

Same wait between every retry:

```python
@app.job(max_retries=5, retry_delay=10)
async def sync_inventory(product_id: str):
    ...
```

Retries at: 10s, 10s, 10s, 10s, 10s.

## Per-attempt delays

Provide a list to set exact delays for each retry:

```python
@app.job(max_retries=3, retry_delay=[1, 5, 30])
async def send_verification_email(user_id: int):
    ...
```

Retries at: 1s, 5s, 30s. If there are more retries than list entries, the last value is reused.

## Exponential backoff

Set `retry_backoff=True` to double the delay on each attempt:

```python
@app.job(max_retries=5, retry_delay=10, retry_backoff=True)
async def call_payment_api(invoice_id: str):
    ...
```

Retries at: 10s, 20s, 40s, 80s, 160s.

If `retry_delay` is `0`, a base of 1 second is used: 1s, 2s, 4s, 8s, 16s.

## Max delay cap

Prevent delays from growing unbounded:

```python
@app.job(max_retries=8, retry_delay=5, retry_backoff=True, retry_max_delay=300)
async def fetch_external_report(report_id: str):
    ...
```

Retries at: 5s, 10s, 20s, 40s, 80s, 160s, 300s, 300s. The delay never exceeds `retry_max_delay`.

## After max retries: the dead-letter queue

When a job exhausts all retries, the row is moved out of `soniq_jobs` and into the *dead-letter queue* - the `soniq_dead_letter_jobs` table, which is a holding area for jobs that failed every retry. The original `soniq_jobs` row is deleted in the same transaction.

Dead-letter jobs do not run on their own. You inspect them, fix the underlying problem, and replay the ones you want re-run. See [Dead-letter queue](../reference/dead-letter.md) for the full inspect / replay workflow.

## Examples

### API call with backoff

External APIs may rate-limit or have intermittent outages. Exponential backoff gives them time to recover.

```python
@app.job(max_retries=5, retry_delay=2, retry_backoff=True, retry_max_delay=120)
async def sync_user_to_crm(user_id: int):
    user = await get_user(user_id)
    async with httpx.AsyncClient() as client:
        resp = await client.post("https://crm.example.com/api/users", json=user.dict())
        resp.raise_for_status()
```

### Email sending with fixed delay

Email servers sometimes reject connections temporarily. A short fixed delay usually works.

```python
@app.job(max_retries=3, retry_delay=5)
async def send_order_confirmation(order_id: str):
    order = await get_order(order_id)
    await smtp_send(to=order.email, subject=f"Order {order_id} confirmed", body=...)
```

### File processing with escalating delays

File imports may fail due to locks or temporary storage issues. Escalate the delay to avoid hammering the system.

```python
@app.job(max_retries=3, retry_delay=[5, 30, 120])
async def import_csv(file_path: str):
    async with aiofiles.open(file_path) as f:
        rows = await parse_csv(f)
    await bulk_insert(rows)
```

## Idempotency

Soniq provides *at-least-once* delivery: a job will run at least once, and may run more than once. Retries are the obvious source of repeats, but a worker that crashes mid-handler will also cause the job to be picked up by another worker and re-run. Every job queue has this property - it is not a Soniq bug, it is a consequence of "do not lose work when machines fail".

The fix is to make your handler *idempotent* - safe to run more than once with the same end result:

- Use `INSERT ... ON CONFLICT DO UPDATE` instead of plain inserts. The second run updates a row that already exists, instead of creating a duplicate.
- Store an idempotency key (often the job arguments hashed, or a unique field on the request) and check it before performing the side effect. If the key already shows the work as done, return.
- Check current state before acting. Before sending an email, look at whether you have already recorded sending it. Before charging a card, look at whether the charge already exists in your payments table.

The pattern is "make the second run a no-op", not "guarantee the second run never happens".

## Job timeouts

Every job has a default execution timeout of 300 seconds (5 minutes). If a job exceeds its timeout, it is treated as a failure and follows normal retry logic.

```python
@app.job(max_retries=3, timeout=60)
async def quick_health_check(service_url: str):
    ...

@app.job(timeout=None)  # no timeout
async def long_running_migration():
    ...
```

Change the global default with `SONIQ_JOB_TIMEOUT`. Set to `0` to disable timeouts globally.
