---
description: "Deliver webhooks reliably from Python with Soniq and PostgreSQL: HMAC-SHA256 signing, retry schedule, idempotency by event ID and transactional enqueue."
---

# Recipe: Webhook Delivery

This recipe delivers webhooks from Soniq background jobs on PostgreSQL. It uses a retry schedule, an idempotency check on the event ID, and HMAC-SHA256 signing.

## The job

```python
import hashlib
import hmac
import json
import aiohttp
from soniq import JobContext, Soniq

soniq = Soniq(database_url="postgresql://localhost/myapp")


@soniq.job(name="webhooks.deliver_webhook", queue="webhooks", retries=5, retry_delay=[1, 5, 30, 120, 600])
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
        await soniq.enqueue(
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

**10-second HTTP timeout.** A hanging endpoint must not hold a worker slot. 10 seconds is enough for a webhook receiver. If the receiver is slower, the job fails and Soniq retries it later.

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

Include these headers with every delivery so receivers can route, verify and deduplicate each delivery:

| Header | Description |
| --- | --- |
| `Content-Type` | `application/json` |
| `X-Webhook-Event` | Event type (e.g. `order.created`) |
| `X-Webhook-Delivery` | Unique delivery ID for deduplication |
| `X-Webhook-Signature` | HMAC-SHA256 signature (`sha256=...`) |
| `User-Agent` | Your app identifier |

Soniq's built-in webhook sender also sends `X-Webhook-Timestamp` (Unix seconds) and two signatures. `X-Webhook-Signature` signs the body only and is kept for existing receivers. `X-Webhook-Signature-V2` signs `f"{timestamp}.{body}"`. New receivers must verify the V2 header. They must also reject requests whose timestamp is more than a few minutes old. Otherwise an attacker can replay a captured request:

```python
import hashlib
import hmac
import time

def verify_v2(body: bytes, headers: dict, secret: str, max_age: int = 300) -> bool:
    timestamp = headers.get("X-Webhook-Timestamp", "")
    if not timestamp.isdigit() or abs(time.time() - int(timestamp)) > max_age:
        return False
    signed = timestamp.encode() + b"." + body
    expected = "sha256=" + hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, headers.get("X-Webhook-Signature-V2", ""))
```

Verify the raw request body, not a re-serialized copy of the parsed JSON. The bytes must match exactly.

On the built-in sender, an endpoint's `max_retries` is the total number of delivery attempts, not the retries after the first one. `max_retries=3` sends at most three requests.

## Transactional enqueue

For critical events, enqueue the webhook inside the same transaction as the business data:

```python
await soniq.ensure_initialized()
async with soniq.backend.acquire() as conn:
    async with conn.transaction():
        order_id = await conn.fetchval("INSERT INTO orders (...) RETURNING id", ...)
        await soniq.enqueue(
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
