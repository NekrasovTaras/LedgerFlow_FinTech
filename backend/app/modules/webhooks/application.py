import asyncio
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from sqlalchemy import and_, or_, select

from app.core.config import get_settings
from app.core.observability import WEBHOOK_FAILURES
from app.infrastructure.database import SessionLocal
from app.infrastructure.models import OutboxEvent, Payment, WebhookDelivery, WebhookSubscription
from app.modules.ledger.application import audit, timeline

MAX_ATTEMPTS = 5
LEASE_SECONDS = 30


@dataclass(frozen=True)
class DeliveryClaim:
    id: str
    event_id: str
    event_type: str
    payload: dict[str, Any]
    created_at: datetime
    subscription_id: str
    owner_id: str
    receiver: str
    lease: datetime
    attempt: int


def allowed_receiver_url(receiver: str) -> str:
    url = get_settings().webhook_receiver_url
    parsed = urlsplit(url)
    if (
        receiver != "mock"
        or parsed.scheme != "http"
        or parsed.hostname not in {"localhost", "127.0.0.1", "receiver", "webhook-receiver"}
        or parsed.port != 9003
        or parsed.path != "/events"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Webhook destination is outside the configured local mock allowlist.")
    return url


def signed_body(claim: DeliveryClaim, timestamp: int | None = None) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(
        {
            "event_id": claim.event_id,
            "event_type": claim.event_type,
            "created_at": claim.created_at.isoformat(),
            "data": claim.payload,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode()
    signed_at = str(int(time.time()) if timestamp is None else timestamp)
    material = signed_at.encode() + b"." + claim.event_id.encode() + b"." + body
    signature = hmac.new(
        get_settings().webhook_secret.encode(), material, hashlib.sha256
    ).hexdigest()
    return body, {
        "Content-Type": "application/json",
        "X-LedgerFlow-Event-ID": claim.event_id,
        "X-LedgerFlow-Timestamp": signed_at,
        "X-LedgerFlow-Signature": f"sha256={signature}",
    }


async def claim_deliveries(limit: int = 25, delivery_id: str | None = None) -> list[DeliveryClaim]:
    now = datetime.now(UTC)
    async with SessionLocal.begin() as session:
        expired = and_(WebhookDelivery.status == "SENDING", WebhookDelivery.next_attempt_at <= now)
        exhausted = (
            await session.scalars(
                select(WebhookDelivery)
                .where(expired, WebhookDelivery.attempts >= MAX_ATTEMPTS)
                .with_for_update(skip_locked=True)
                .limit(100)
            )
        ).all()
        for row in exhausted:
            row.status = "DEAD"
            row.last_error = "Final webhook lease expired; operator replay required."
        query = (
            select(WebhookDelivery, WebhookSubscription, OutboxEvent)
            .join(WebhookSubscription, WebhookDelivery.subscription_id == WebhookSubscription.id)
            .join(OutboxEvent, WebhookDelivery.event_id == OutboxEvent.id)
            .where(
                WebhookDelivery.attempts < MAX_ATTEMPTS,
                or_(
                    and_(
                        WebhookDelivery.status.in_(["PENDING", "RETRY"]),
                        WebhookDelivery.next_attempt_at <= now,
                    ),
                    expired,
                ),
            )
            .order_by(WebhookDelivery.created_at, WebhookDelivery.id)
            .limit(max(1, min(limit, 100)))
            .with_for_update(skip_locked=True, of=WebhookDelivery)
        )
        if delivery_id:
            query = query.where(WebhookDelivery.id == str(UUID(delivery_id)))
        claims = []
        for delivery, subscription, event in (await session.execute(query)).all():
            if not subscription.active:
                delivery.status = "CANCELED"
                delivery.last_error = "Subscription disabled."
                continue
            delivery.status = "SENDING"
            delivery.attempts += 1
            delivery.next_attempt_at = now + timedelta(seconds=LEASE_SECONDS)
            claims.append(
                DeliveryClaim(
                    delivery.id,
                    event.id,
                    event.event_type,
                    event.payload,
                    event.created_at,
                    subscription.id,
                    subscription.owner_id,
                    subscription.receiver,
                    delivery.next_attempt_at,
                    delivery.attempts,
                )
            )
    return claims


async def settle_delivery(claim: DeliveryClaim, http_status: int | None, error: str | None) -> str:
    now = datetime.now(UTC)
    async with SessionLocal.begin() as session:
        row = await session.scalar(
            select(WebhookDelivery).where(WebhookDelivery.id == claim.id).with_for_update()
        )
        if (
            row is None
            or row.status != "SENDING"
            or row.next_attempt_at != claim.lease
            or row.attempts != claim.attempt
            or row.next_attempt_at <= now
        ):
            return "STALE"
        if error is None:
            row.status, row.last_error = "DELIVERED", None
        else:
            row.status = "DEAD" if row.attempts >= MAX_ATTEMPTS else "RETRY"
            row.last_error = error[:500]
            row.next_attempt_at = now + timedelta(seconds=min(2**row.attempts, 60))
            WEBHOOK_FAILURES.inc()
        details = {
            "event_id": claim.event_id,
            "attempt": claim.attempt,
            "status": row.status,
            "http_status": http_status,
            "error": error,
        }
        audit(session, claim.owner_id, "webhook.attempt", row.id, details)
        payment_id = claim.payload.get("payment_id")
        if isinstance(payment_id, str):
            try:
                payment_id = str(UUID(payment_id))
            except ValueError:
                payment_id = None
            payment = await session.get(Payment, payment_id) if payment_id else None
            if payment is not None:
                timeline(
                    session,
                    payment.id,
                    "webhook.attempt",
                    "Webhook attempt recorded",
                    f"Attempt {claim.attempt}; HTTP {http_status or 'unavailable'}",
                    row.status,
                    details,
                )
        return row.status


async def deliver_claim(claim: DeliveryClaim) -> dict[str, Any]:
    http_status = None
    error_message = None
    try:
        url = allowed_receiver_url(claim.receiver)
        body, headers = signed_body(claim)
        async with httpx.AsyncClient(timeout=2, trust_env=False, follow_redirects=False) as client:
            response = await client.post(url, content=body, headers=headers)
        http_status = response.status_code
        if not 200 <= response.status_code < 300:
            error_message = f"Receiver returned HTTP {response.status_code}."
        else:
            acknowledgement = response.json()
            if (
                acknowledgement.get("accepted") is not True
                or acknowledgement.get("event_id") != claim.event_id
            ):
                error_message = "Receiver acknowledgement did not match the event."
    except Exception as error:
        error_message = f"Webhook delivery failure: {type(error).__name__}"
    status = await settle_delivery(claim, http_status, error_message)
    return {
        "delivery_id": claim.id,
        "event_id": claim.event_id,
        "status": status,
        "attempts": claim.attempt,
        "http_status": http_status,
    }


async def deliver_batch(limit: int = 25) -> list[dict[str, Any]]:
    claims = await claim_deliveries(min(limit, 25))
    semaphore = asyncio.Semaphore(5)

    async def deliver_bounded(claim: DeliveryClaim) -> dict[str, Any]:
        async with semaphore:
            return await deliver_claim(claim)

    return await asyncio.gather(*(deliver_bounded(claim) for claim in claims))
