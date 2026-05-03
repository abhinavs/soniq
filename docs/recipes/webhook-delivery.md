# Recipe: Webhook Delivery

A pattern for reliably delivering webhooks to external services with aggressive retries, idempotency, and HMAC signing.

## The job

```python
import hashlib
import hmac
import json
import aiohttp
from soniq import Soniq
from soniq.job import JobContext

eq = Soniq(database_url="postgresql://localhost/myapp")


@eq.job(queue="webhooks", max_retries=5, retry_delay=[1, 5, 30, 120, 600])
async def deliver_webhook(
    url: str,
    event: str,
    payload: dict,
    event_id: str,
    secret: str,
    ctx: JobContext,
):
    # Idempotency: check if this event was already delivered
    if await was_delivered(event_id):
        return

    body = json.dumps(payload).encode()
    signature = sign_payload(body, secret)

    headers = {
        "Content-Type": "application/json",
        "X-Webhook-Event": event,
        "X-Webhook-Delivery": event_id,
        "X-Webhook-Signature": signature,
        "User-Agent": "MyApp-Webhook/1.0",
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, data=body, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status >= 400:
                raise RuntimeError(f"Webhook delivery failed: HTTP {resp.status}")

    await mark_delivered(event_id)
```

## Signing payloads

Sign every payload with HMAC-SHA256 so receivers can verify authenticity:

```python
def sign_payload(body: bytes, secret: str) -> str:
    digest = hmac.new(
        secret.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()
    return f"sha256={digest}"
```

The receiver verifies with the same logic:

```python
def verify_signature(payload_bytes: bytes, secret: str, signature: str) -> bool:
    expected = sign_payload(payload_bytes, secret)
    return hmac.compare_digest(expected, signature)
```

## Enqueuing

When a business event occurs, enqueue the webhook delivery:

```python
import uuid

async def notify_subscribers(event: str, data: dict):
    subscribers = await get_webhook_subscribers(event)
    event_id_base = str(uuid.uuid4())

    for sub in subscribers:
        await eq.enqueue(
            deliver_webhook,
            url=sub.url,
            event=event,
            payload={"event": event, "data": data},
            event_id=f"{event_id_base}-{sub.id}",
            secret=sub.secret,
        )
```

Each subscriber gets their own job with a unique `event_id`. If one subscriber's endpoint is down, it doesn't block deliveries to others.

## Why this works

**Aggressive retry schedule.** `retry_delay=[1, 5, 30, 120, 600]` retries at 1 second, 5 seconds, 30 seconds, 2 minutes, and 10 minutes. Most transient failures recover within the first few retries. The longer tail gives endpoints time to come back from extended outages.

**Idempotency via event_id.** The `event_id` uniquely identifies each delivery attempt. If a worker crashes after the HTTP request succeeds but before marking the job done, the retry checks `was_delivered(event_id)` and skips the duplicate. Implement this with a simple database table:

```python
async def was_delivered(event_id: str) -> bool:
    return await db.fetchval(
        "SELECT EXISTS(SELECT 1 FROM webhook_deliveries WHERE event_id = $1)",
        event_id,
    )

async def mark_delivered(event_id: str):
    await db.execute(
        "INSERT INTO webhook_deliveries (event_id) VALUES ($1) ON CONFLICT DO NOTHING",
        event_id,
    )
```

**Dedicated queue.** Webhook delivery is I/O-bound (waiting on external HTTP responses) and failure-prone. A separate `"webhooks"` queue means slow or failing endpoints don't starve your other jobs.

**10-second HTTP timeout.** Don't let a hanging endpoint tie up a worker slot forever. 10 seconds is generous for a webhook receiver. If they can't respond in time, retry later.

## Payload format

Follow a consistent structure for all webhook payloads:

```json
{
  "event": "order.created",
  "data": {
    "order_id": "ord_abc123",
    "total": 99.99,
    "currency": "USD",
    "created_at": "2026-03-28T14:30:00Z"
  }
}
```

## Headers

Include these headers with every delivery so receivers can route, verify, and deduplicate:

| Header | Description |
| --- | --- |
| `Content-Type` | `application/json` |
| `X-Webhook-Event` | Event type (e.g. `order.created`) |
| `X-Webhook-Delivery` | Unique delivery ID for deduplication |
| `X-Webhook-Signature` | HMAC-SHA256 signature (`sha256=...`) |
| `User-Agent` | Your app identifier |

## Transactional enqueue

For critical events, enqueue the webhook inside the same transaction as the business data:

```python
await eq.ensure_initialized()
async with eq.backend.acquire() as conn:
    async with conn.transaction():
        order_id = await conn.fetchval("INSERT INTO orders (...) RETURNING id", ...)
        await eq.enqueue(
            deliver_webhook,
            connection=conn,
            url=subscriber.url,
            event="order.created",
            payload={"event": "order.created", "data": {"order_id": order_id}},
            event_id=f"order-created-{order_id}",
            secret=subscriber.secret,
        )
```

If the order INSERT fails, the webhook job is never created.
