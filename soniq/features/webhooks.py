"""
Webhook System for Job Lifecycle Notifications.
HTTP notifications for job events (success, failure, dead letter).
"""

import asyncio
import hashlib
import hmac
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import (
    TYPE_CHECKING,
    Any,
    AsyncIterator,
    Dict,
    List,
    Optional,
    Protocol,
    runtime_checkable,
)

try:
    import aiohttp
except ImportError:
    aiohttp = None  # type: ignore[assignment]


from .signing import SecureWebhookSecret

if TYPE_CHECKING:
    from soniq.app import Soniq

logger = logging.getLogger(__name__)


def _require_aiohttp():
    if aiohttp is None:
        raise ImportError(
            "aiohttp is required for webhooks. "
            "Install it with: pip install soniq[webhooks]"
        )


class WebhookEvent(str, Enum):
    """Webhook event types"""

    JOB_QUEUED = "job.queued"
    JOB_STARTED = "job.started"
    JOB_COMPLETED = "job.completed"
    JOB_FAILED = "job.failed"
    JOB_RETRIED = "job.retried"
    JOB_DEAD_LETTER = "job.dead_letter"
    JOB_RESURRECTED = "job.resurrected"
    QUEUE_BACKLOG = "queue.backlog"
    SYSTEM_ALERT = "system.alert"


@dataclass
class WebhookPayload:
    """Webhook payload structure"""

    event: str
    timestamp: str
    data: Dict[str, Any]
    metadata: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class WebhookEndpoint:
    """Webhook endpoint configuration"""

    id: str
    url: str
    secret: Optional[str] = None
    events: Optional[List[str]] = None  # None means all events
    active: bool = True
    max_retries: int = 3
    timeout_seconds: int = 30
    headers: Optional[Dict[str, str]] = None
    _secure_secret: Optional[SecureWebhookSecret] = None

    def __post_init__(self):
        if self.events is None:
            self.events = [event.value for event in WebhookEvent]

        # Initialize secure secret wrapper
        if self.secret:
            self._secure_secret = SecureWebhookSecret(self.secret)
            # Store encrypted version in the secret field for database storage
            self.secret = self._secure_secret.encrypted

    @property
    def plaintext_secret(self) -> Optional[str]:
        """Get the plaintext secret for signing/verification"""
        if self._secure_secret:
            return self._secure_secret.plaintext
        return None

    @property
    def encrypted_secret(self) -> Optional[str]:
        """Get the encrypted secret for database storage"""
        return self.secret


@dataclass
class WebhookDelivery:
    """Webhook delivery record"""

    id: str
    endpoint_id: str
    event: str
    payload: Dict[str, Any]
    status: str  # pending, delivered, failed, expired
    attempts: int = 0
    max_attempts: int = 3
    next_retry_at: Optional[datetime] = None
    last_error: Optional[str] = None
    response_status: Optional[int] = None
    response_body: Optional[str] = None
    created_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now(timezone.utc)


class WebhookSigner:
    """Webhook payload signing"""

    @staticmethod
    def sign_payload(payload: str, secret: str) -> str:
        """Sign webhook payload with HMAC-SHA256"""
        signature = hmac.new(
            secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        return f"sha256={signature}"

    @staticmethod
    def verify_signature(payload: str, signature: str, secret: str) -> bool:
        """Verify webhook signature"""
        expected_signature = WebhookSigner.sign_payload(payload, secret)
        return hmac.compare_digest(signature, expected_signature)


@dataclass
class WebhookResult:
    """Outcome of a single transport ``deliver`` call.

    ``ok`` is True iff the remote responded with a 2xx; the dispatcher
    treats anything else as a delivery failure and schedules a retry.
    ``status`` and ``body`` are echoed into the persisted delivery record
    so operators can debug 5xx without re-fetching the upstream.
    """

    ok: bool
    status: Optional[int] = None
    body: Optional[str] = None
    error: Optional[str] = None


@runtime_checkable
class WebhookTransport(Protocol):
    """Pluggable webhook delivery mechanism.

    Storage (which endpoints exist, retry state) stays tied to the queue's
    database. Delivery (the actual outbound request) is the seam: tests
    swap in an in-memory transport, ops can route through Lambda / SQS /
    SNS / a vault-aware HTTP client by implementing this single method.
    """

    async def deliver(
        self,
        *,
        url: str,
        payload: bytes,
        headers: Dict[str, str],
    ) -> WebhookResult: ...


class HTTPTransport:
    """Default ``WebhookTransport`` backed by ``aiohttp``.

    A new ``ClientSession`` is opened per delivery so the transport stays
    safe to construct from sync code (no event loop bound at import time).
    Connection reuse is on the to-do list; the dispatcher already caps
    concurrency via a semaphore so this is rarely the bottleneck.
    """

    def __init__(self, *, timeout_seconds: int = 30):
        self._default_timeout = timeout_seconds

    async def deliver(
        self,
        *,
        url: str,
        payload: bytes,
        headers: Dict[str, str],
    ) -> WebhookResult:
        _require_aiohttp()
        timeout = aiohttp.ClientTimeout(total=self._default_timeout)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, data=payload, headers=headers) as response:
                    # Cap response body at 4KB to prevent OOM from large responses
                    body = (await response.content.read(4096)).decode(
                        "utf-8", errors="replace"
                    )
                    return WebhookResult(
                        ok=200 <= response.status < 300,
                        status=response.status,
                        body=body,
                        error=(
                            None
                            if 200 <= response.status < 300
                            else f"HTTP {response.status}"
                        ),
                    )
        except Exception as e:
            return WebhookResult(ok=False, status=None, body=None, error=str(e))


class WebhookRegistry:
    """Registry for webhook endpoints.

    Holds the in-memory endpoint cache and persists changes to Postgres
    through ``self._app.backend.acquire()``. Constructed by
    ``WebhookService``; code that wants to drop in a custom backend can
    pass any ``Soniq``-like object whose ``backend`` exposes
    ``acquire()``.
    """

    def __init__(self, app: "Soniq"):
        self._app = app
        self.endpoints: Dict[str, WebhookEndpoint] = {}
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def _acquire(self) -> AsyncIterator[Any]:
        await self._app.ensure_initialized()
        async with self._app.backend.acquire() as conn:
            yield conn

    async def register_endpoint(self, endpoint: WebhookEndpoint) -> str:
        """Register a webhook endpoint"""
        async with self._lock:
            endpoint_id = endpoint.id or str(uuid.uuid4())
            endpoint.id = endpoint_id
            self.endpoints[endpoint_id] = endpoint

            # Persist to database
            await self._save_endpoint_to_db(endpoint)

        logger.info(f"Registered webhook endpoint: {endpoint_id} -> {endpoint.url}")
        return endpoint_id

    async def unregister_endpoint(self, endpoint_id: str) -> bool:
        """Unregister a webhook endpoint"""
        async with self._lock:
            if endpoint_id in self.endpoints:
                del self.endpoints[endpoint_id]
                await self._delete_endpoint_from_db(endpoint_id)
                logger.info(f"Unregistered webhook endpoint: {endpoint_id}")
                return True
        return False

    async def get_endpoint(self, endpoint_id: str) -> Optional[WebhookEndpoint]:
        """Get webhook endpoint by ID"""
        return self.endpoints.get(endpoint_id)

    async def get_endpoints_for_event(
        self, event: WebhookEvent
    ) -> List[WebhookEndpoint]:
        """Get all active endpoints that subscribe to an event"""
        return [
            endpoint
            for endpoint in self.endpoints.values()
            if endpoint.active
            and (not endpoint.events or event.value in endpoint.events)
        ]

    async def list_endpoints(self) -> List[WebhookEndpoint]:
        """List all registered endpoints"""
        return list(self.endpoints.values())

    async def update_endpoint(self, endpoint_id: str, **updates) -> bool:
        """Update webhook endpoint configuration"""
        async with self._lock:
            if endpoint_id in self.endpoints:
                endpoint = self.endpoints[endpoint_id]
                for key, value in updates.items():
                    if hasattr(endpoint, key):
                        setattr(endpoint, key, value)

                await self._save_endpoint_to_db(endpoint)
                logger.info(f"Updated webhook endpoint: {endpoint_id}")
                return True
        return False

    async def _save_endpoint_to_db(self, endpoint: WebhookEndpoint):
        """Save endpoint configuration to database"""
        async with self._acquire() as conn:
            await conn.execute(
                """
                INSERT INTO soniq_webhook_endpoints (
                    id, url, secret, events, active, max_retries, timeout_seconds, headers
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (id) DO UPDATE SET
                    url = EXCLUDED.url,
                    secret = EXCLUDED.secret,
                    events = EXCLUDED.events,
                    active = EXCLUDED.active,
                    max_retries = EXCLUDED.max_retries,
                    timeout_seconds = EXCLUDED.timeout_seconds,
                    headers = EXCLUDED.headers,
                    updated_at = NOW()
            """,
                endpoint.id,
                endpoint.url,
                endpoint.secret,
                endpoint.events,
                endpoint.active,
                endpoint.max_retries,
                endpoint.timeout_seconds,
                endpoint.headers,
            )

    async def _delete_endpoint_from_db(self, endpoint_id: str):
        """Delete endpoint from database"""
        async with self._acquire() as conn:
            await conn.execute(
                "DELETE FROM soniq_webhook_endpoints WHERE id = $1", endpoint_id
            )

    async def load_endpoints_from_db(self):
        """Load endpoints from database on startup"""
        try:
            async with self._acquire() as conn:
                endpoints = await conn.fetch(
                    """
                    SELECT * FROM soniq_webhook_endpoints WHERE active = true
                """
                )

                async with self._lock:
                    for row in endpoints:
                        # JSONB columns are decoded by the pool codec.
                        endpoint = WebhookEndpoint(
                            id=row["id"],
                            url=row["url"],
                            secret=row["secret"],
                            events=row["events"],
                            active=row["active"],
                            max_retries=row["max_retries"],
                            timeout_seconds=row["timeout_seconds"],
                            headers=row["headers"],
                        )
                        self.endpoints[endpoint.id] = endpoint

                logger.info(f"Loaded {len(endpoints)} webhook endpoints from database")

        except Exception as e:
            logger.error(f"Failed to load webhook endpoints: {e}")


class WebhookDispatcher:
    """Webhook event dispatcher and delivery manager.

    Pool access for retry-processor reads and delivery-record writes is
    routed through the same ``Soniq`` instance the registry was bound to,
    via ``self.registry._app``.
    """

    def __init__(
        self,
        registry: WebhookRegistry,
        max_concurrent_deliveries: int = 10,
        *,
        transport: Optional[WebhookTransport] = None,
    ):
        self.registry = registry
        self.max_concurrent_deliveries = max_concurrent_deliveries
        self.delivery_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self.delivery_semaphore = asyncio.Semaphore(max_concurrent_deliveries)
        self._delivery_workers: List[asyncio.Task] = []
        self._running = False
        self.transport: WebhookTransport = transport or HTTPTransport()

    def _acquire(self):
        return self.registry._acquire()

    async def start(self):
        """Start webhook delivery workers"""
        if self._running:
            return

        self._running = True

        # Start delivery workers
        for i in range(self.max_concurrent_deliveries):
            worker = asyncio.create_task(self._delivery_worker(f"worker-{i}"))
            self._delivery_workers.append(worker)

        # Start retry processor
        retry_worker = asyncio.create_task(self._retry_processor())
        self._delivery_workers.append(retry_worker)

        logger.info(
            f"Started webhook dispatcher with {self.max_concurrent_deliveries} workers"
        )

    async def stop(self):
        """Stop webhook delivery workers"""
        if not self._running:
            return

        self._running = False

        # Cancel all workers
        for worker in self._delivery_workers:
            worker.cancel()

        # Wait for workers to finish
        await asyncio.gather(*self._delivery_workers, return_exceptions=True)
        self._delivery_workers.clear()

        logger.info("Stopped webhook dispatcher")

    async def dispatch_event(
        self,
        event: WebhookEvent,
        data: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """Dispatch webhook event to all subscribed endpoints"""
        endpoints = await self.registry.get_endpoints_for_event(event)

        if not endpoints:
            logger.debug(f"No webhook endpoints for event: {event.value}")
            return

        payload = WebhookPayload(
            event=event.value,
            timestamp=datetime.now(timezone.utc).isoformat(),
            data=data,
            metadata=metadata,
        )

        # Create delivery records for each endpoint
        dropped = 0
        for endpoint in endpoints:
            delivery = WebhookDelivery(
                id=str(uuid.uuid4()),
                endpoint_id=endpoint.id,
                event=event.value,
                payload=payload.to_dict(),
                status="pending",
                max_attempts=endpoint.max_retries,
            )

            # Non-blocking enqueue. The dispatcher uses backpressure (a
            # bounded queue) on purpose: if delivery is slower than
            # production, we'd rather drop and warn than build an
            # unbounded backlog that delays application shutdown. The
            # retry table picks up failed deliveries from the DB, so a
            # drop here is recoverable.
            try:
                self.delivery_queue.put_nowait(delivery)
            except asyncio.QueueFull:
                dropped += 1

        if dropped:
            logger.warning(
                "Webhook delivery queue full; dropped %d/%d deliveries for %s "
                "(queue maxsize=%d). Retry processor will pick them up from the "
                "database on the next cycle.",
                dropped,
                len(endpoints),
                event.value,
                self.delivery_queue.maxsize,
            )

        logger.info(
            "Dispatched %s to %d endpoints (%d dropped)",
            event.value,
            len(endpoints) - dropped,
            dropped,
        )

    async def _delivery_worker(self, worker_name: str):
        """Worker to process webhook deliveries"""
        while self._running:
            try:
                # Get delivery from queue
                delivery = await asyncio.wait_for(
                    self.delivery_queue.get(), timeout=1.0
                )

                # Process delivery
                async with self.delivery_semaphore:
                    await self._process_delivery(delivery)

            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.exception(f"Delivery worker {worker_name} error: {e}")
                await asyncio.sleep(5)

    async def _process_delivery(self, delivery: WebhookDelivery):
        """Process a single webhook delivery"""
        endpoint = await self.registry.get_endpoint(delivery.endpoint_id)
        if not endpoint or not endpoint.active:
            logger.warning(f"Endpoint {delivery.endpoint_id} not found or inactive")
            return

        delivery.attempts += 1

        # Prepare payload
        payload_json = json.dumps(delivery.payload)

        # Prepare headers
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Soniq-Webhook/1.0",
            "X-Webhook-Event": delivery.event,
            "X-Webhook-Delivery": delivery.id,
            "X-Webhook-Timestamp": str(int(time.time())),
        }
        if endpoint.headers:
            headers.update(endpoint.headers)
        if endpoint.plaintext_secret:
            headers["X-Webhook-Signature"] = WebhookSigner.sign_payload(
                payload_json, endpoint.plaintext_secret
            )

        result = await self.transport.deliver(
            url=endpoint.url,
            payload=payload_json.encode("utf-8"),
            headers=headers,
        )
        delivery.response_status = result.status
        delivery.response_body = result.body

        if result.ok:
            delivery.status = "delivered"
            delivery.delivered_at = datetime.now(timezone.utc)
            logger.info(f"Webhook delivered: {delivery.id} -> {endpoint.url}")
        else:
            delivery.last_error = result.error or "delivery failed"
            if delivery.attempts >= delivery.max_attempts:
                delivery.status = "failed"
                logger.error(
                    f"Webhook delivery failed permanently: {delivery.id} -> "
                    f"{endpoint.url}: {delivery.last_error}"
                )
            else:
                # Exponential backoff capped at 5 minutes
                delay_seconds = min(300, 2 ** (delivery.attempts - 1) * 60)
                delivery.next_retry_at = datetime.now(timezone.utc).replace(
                    microsecond=0
                ) + timedelta(seconds=delay_seconds)
                delivery.status = "pending"
                logger.warning(
                    f"Webhook delivery failed, will retry: {delivery.id} -> "
                    f"{endpoint.url}: {delivery.last_error}"
                )

        await self._save_delivery_record(delivery)

    async def _retry_processor(self):
        """Process webhook delivery retries"""
        while self._running:
            try:
                # Find deliveries ready for retry
                async with self._acquire() as conn:
                    deliveries = await conn.fetch(
                        """
                        SELECT * FROM soniq_webhook_deliveries
                        WHERE status = 'pending'
                        AND next_retry_at IS NOT NULL
                        AND next_retry_at <= NOW()
                        ORDER BY next_retry_at
                        LIMIT 100
                        FOR UPDATE SKIP LOCKED
                    """
                    )

                    for row in deliveries:
                        delivery = WebhookDelivery(
                            id=row["id"],
                            endpoint_id=row["endpoint_id"],
                            event=row["event"],
                            payload=row["payload"],
                            status=row["status"],
                            attempts=row["attempts"],
                            max_attempts=row["max_attempts"],
                            next_retry_at=row["next_retry_at"],
                            last_error=row["last_error"],
                            response_status=row["response_status"],
                            response_body=row["response_body"],
                            created_at=row["created_at"],
                            delivered_at=row["delivered_at"],
                        )

                        # Re-queue for delivery without blocking the retry
                        # scanner loop when the queue is saturated.
                        try:
                            self.delivery_queue.put_nowait(delivery)
                        except asyncio.QueueFull:
                            logger.warning(
                                "Webhook retry queue full; deferring delivery %s "
                                "until the next retry scan cycle",
                                delivery.id,
                            )

                await asyncio.sleep(30)  # Check every 30 seconds

            except Exception as e:
                logger.exception(f"Retry processor error: {e}")
                await asyncio.sleep(60)

    async def _save_delivery_record(self, delivery: WebhookDelivery):
        """Save delivery record to database"""
        try:
            async with self._acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO soniq_webhook_deliveries (
                        id, endpoint_id, event, payload, status, attempts, max_attempts,
                        next_retry_at, last_error, response_status, response_body,
                        created_at, delivered_at
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
                    ON CONFLICT (id) DO UPDATE SET
                        status = EXCLUDED.status,
                        attempts = EXCLUDED.attempts,
                        next_retry_at = EXCLUDED.next_retry_at,
                        last_error = EXCLUDED.last_error,
                        response_status = EXCLUDED.response_status,
                        response_body = EXCLUDED.response_body,
                        delivered_at = EXCLUDED.delivered_at
                """,
                    delivery.id,
                    delivery.endpoint_id,
                    delivery.event,
                    delivery.payload,
                    delivery.status,
                    delivery.attempts,
                    delivery.max_attempts,
                    delivery.next_retry_at,
                    delivery.last_error,
                    delivery.response_status,
                    delivery.response_body,
                    delivery.created_at,
                    delivery.delivered_at,
                )
        except Exception as e:
            logger.error(f"Failed to save delivery record: {e}")


class WebhookService:
    """High-level webhook interface bound to a Soniq instance."""

    def __init__(
        self,
        app: "Soniq",
        *,
        transport: Optional[WebhookTransport] = None,
    ):
        self._app = app
        self.registry = WebhookRegistry(app)
        self.dispatcher = WebhookDispatcher(
            self.registry, transport=transport or HTTPTransport()
        )
        self._started = False

    @property
    def transport(self) -> WebhookTransport:
        """The transport this service routes deliveries through."""
        return self.dispatcher.transport

    def _acquire(self):
        return self.registry._acquire()

    async def start(self):
        """Start webhook system"""
        if self._started:
            return

        await self.registry.load_endpoints_from_db()
        await self.dispatcher.start()
        self._started = True
        logger.info("Webhook system started")

    async def stop(self):
        """Stop webhook system"""
        if not self._started:
            return

        await self.dispatcher.stop()
        self._started = False
        logger.info("Webhook system stopped")

    async def register(
        self,
        url: str,
        events: Optional[List[str]] = None,
        secret: Optional[str] = None,
        **kwargs,
    ) -> str:
        """Register a new webhook endpoint."""
        endpoint = WebhookEndpoint(
            id=str(uuid.uuid4()), url=url, secret=secret, events=events, **kwargs
        )
        return await self.registry.register_endpoint(endpoint)  # type: ignore[no-any-return]

    async def unregister(self, endpoint_id: str) -> bool:
        """Unregister a webhook endpoint."""
        return await self.registry.unregister_endpoint(endpoint_id)  # type: ignore[no-any-return]

    async def send_webhook(
        self,
        event: WebhookEvent,
        data: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """Send webhook event"""
        if not self._started:
            logger.warning("Webhook system not started, ignoring event")
            return

        await self.dispatcher.dispatch_event(event, data, metadata)

    async def get_delivery_stats(self, hours: int = 24) -> Dict[str, Any]:
        """Get webhook delivery statistics"""
        async with self._acquire() as conn:
            stats = await conn.fetchrow(
                """
                SELECT
                    COUNT(*) as total_deliveries,
                    SUM(CASE WHEN status = 'delivered' THEN 1 ELSE 0 END) as successful_deliveries,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed_deliveries,
                    SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending_deliveries,
                    AVG(attempts) as avg_attempts
                FROM soniq_webhook_deliveries
                WHERE created_at >= NOW() - ($1 || ' hours')::INTERVAL
            """,
                str(hours),
            )

            # Get delivery trends
            trends = await conn.fetch(
                """
                SELECT
                    DATE_TRUNC('hour', created_at) as hour,
                    COUNT(*) as total,
                    SUM(CASE WHEN status = 'delivered' THEN 1 ELSE 0 END) as successful
                FROM soniq_webhook_deliveries
                WHERE created_at >= NOW() - ($1 || ' hours')::INTERVAL
                GROUP BY hour
                ORDER BY hour
            """,
                str(hours),
            )

            return {
                "summary": dict(stats),
                "hourly_trends": [dict(row) for row in trends],
            }


def verify_webhook_signature(payload: str, signature: str, secret: str) -> bool:
    """Verify webhook signature with HMAC-SHA256."""
    return WebhookSigner.verify_signature(payload, signature, secret)
