"""PostgreSQL outbox leases and RabbitMQ confirms; inbox and effect commit together."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import aio_pika
from aio_pika.abc import AbstractChannel, AbstractExchange, AbstractIncomingMessage, AbstractQueue
from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import make_url
from yarl import URL

from app.core.config import get_settings
from app.core.observability import OUTBOX_BACKLOG
from app.infrastructure.database import SessionLocal
from app.infrastructure.models import (
    InboxEvent,
    OutboxEvent,
    WebhookDelivery,
    WebhookSubscription,
    new_id,
)

logger = logging.getLogger("ledgerflow.events")
MAX_ATTEMPTS = 5
LEASE_SECONDS = 30
PUBLISH_TIMEOUT = 5
CONSUMER = "webhook-deliveries-v1"


@dataclass(frozen=True)
class EventClaim:
    id: str
    event_type: str
    payload: dict[str, Any]
    created_at: datetime
    lease: datetime
    attempt: int
    isolated: bool = False


def transport_suffix() -> str:
    """Keep disposable database tests out of the running application's queues."""
    database = make_url(get_settings().database_url).database or ""
    if database.endswith("_test"):
        safe_name = "".join(char for char in database if char.isalnum() or char == "_")
        return f".{safe_name}"
    return ""


def broker_url() -> str:
    # Compose publishes AMQP on IPv4 loopback. On Windows, resolving localhost
    # through IPv6 can establish TCP yet stall AMQP negotiation through Docker.
    url = URL(get_settings().rabbitmq_url)
    return str(url.with_host("127.0.0.1") if url.host == "localhost" else url)


def exchange_name() -> str:
    return f"ledgerflow.events{transport_suffix()}"


def queue_name() -> str:
    return f"ledgerflow.events.consumer{transport_suffix()}"


async def declare_topology(channel: AbstractChannel) -> tuple[AbstractExchange, AbstractQueue]:
    suffix = transport_suffix()
    dead_exchange = await channel.declare_exchange(
        f"ledgerflow.events.dlx{suffix}", aio_pika.ExchangeType.DIRECT, durable=True
    )
    dead_queue = await channel.declare_queue(f"ledgerflow.events.dlq{suffix}", durable=True)
    await dead_queue.bind(dead_exchange, "dead")
    exchange = await channel.declare_exchange(
        exchange_name(), aio_pika.ExchangeType.TOPIC, durable=True
    )
    queue = await channel.declare_queue(
        queue_name(),
        durable=True,
        arguments={
            "x-dead-letter-exchange": dead_exchange.name,
            "x-dead-letter-routing-key": "dead",
        },
    )
    await queue.bind(exchange, "#")
    await channel.set_qos(prefetch_count=20)
    return exchange, queue


def event_bytes(claim: EventClaim) -> bytes:
    return json.dumps(
        {
            "event_id": claim.id,
            "event_type": claim.event_type,
            "created_at": claim.created_at.isoformat(),
            "data": claim.payload,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode()


async def claim_events(limit: int = 25, event_id: str | None = None) -> list[EventClaim]:
    now = datetime.now(UTC)
    async with SessionLocal.begin() as session:
        expired = and_(OutboxEvent.status == "PUBLISHING", OutboxEvent.locked_until <= now)
        exhausted_condition = expired
        if event_id is not None:
            exhausted_condition = or_(
                expired,
                and_(
                    OutboxEvent.id == str(UUID(event_id)),
                    OutboxEvent.status == "ISOLATED_PUBLISHING",
                    OutboxEvent.locked_until <= now,
                ),
            )
        # A crash on the final attempt must become visible rather than stay leased forever.
        exhausted = (
            await session.scalars(
                select(OutboxEvent)
                .where(exhausted_condition, OutboxEvent.attempts >= MAX_ATTEMPTS)
                .with_for_update(skip_locked=True)
                .limit(100)
            )
        ).all()
        for row in exhausted:
            row.status = "DEAD"
            row.locked_until = None
            row.last_error = "Final publisher lease expired; operator replay required."
        eligible = or_(
            and_(
                OutboxEvent.status.in_(["PENDING", "RETRY"]),
                OutboxEvent.available_at <= now,
            ),
            expired,
        )
        if event_id is not None:
            eligible = or_(
                eligible,
                OutboxEvent.status == "ISOLATED",
                and_(OutboxEvent.status == "ISOLATED_PUBLISHING", OutboxEvent.locked_until <= now),
            )
        query = (
            select(OutboxEvent)
            .where(
                OutboxEvent.attempts < MAX_ATTEMPTS,
                eligible,
            )
            .order_by(OutboxEvent.created_at, OutboxEvent.id)
            .limit(max(1, min(limit, 100)))
            .with_for_update(skip_locked=True)
        )
        if event_id is not None:
            query = query.where(OutboxEvent.id == str(UUID(event_id)))
        claims = []
        for row in (await session.scalars(query)).all():
            isolated = row.status in {"ISOLATED", "ISOLATED_PUBLISHING"}
            row.status = "ISOLATED_PUBLISHING" if isolated else "PUBLISHING"
            row.attempts += 1
            row.locked_until = now + timedelta(seconds=LEASE_SECONDS)
            claims.append(
                EventClaim(
                    row.id,
                    row.event_type,
                    row.payload,
                    row.created_at,
                    row.locked_until,
                    row.attempts,
                    isolated,
                )
            )
    return claims


async def settle_claim(claim: EventClaim, error: str | None = None) -> str:
    now = datetime.now(UTC)
    async with SessionLocal.begin() as session:
        row = await session.scalar(
            select(OutboxEvent).where(OutboxEvent.id == claim.id).with_for_update()
        )
        if (
            row is None
            or row.status != ("ISOLATED_PUBLISHING" if claim.isolated else "PUBLISHING")
            or row.locked_until != claim.lease
            or row.attempts != claim.attempt
            or row.locked_until <= now
        ):
            return "STALE"
        row.locked_until = None
        if error is None:
            row.status = "PUBLISHED"
            row.published_at = now
            row.last_error = None
        else:
            row.last_error = error[:500]
            row.status = (
                "DEAD"
                if row.attempts >= MAX_ATTEMPTS
                else "ISOLATED"
                if claim.isolated
                else "RETRY"
            )
            row.available_at = now + timedelta(seconds=min(2**row.attempts, 60))
        return row.status


async def publish_claim(claim: EventClaim, *, fault_disconnect: bool = False) -> dict[str, Any]:
    try:
        # The timeout covers connection, declarations, mandatory publish and confirm.
        # No database transaction or financial row lock is open during network IO.
        async with asyncio.timeout(PUBLISH_TIMEOUT):
            async with publisher_route(fault_disconnect) as url:
                connection = await aio_pika.connect(url, timeout=3)
                async with connection:
                    channel = await connection.channel(
                        publisher_confirms=True, on_return_raises=True
                    )
                    exchange, _ = await declare_topology(channel)
                    confirmation = await exchange.publish(
                        aio_pika.Message(
                            event_bytes(claim),
                            message_id=claim.id,
                            type=claim.event_type,
                            content_type="application/json",
                            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                            timestamp=claim.created_at,
                            headers={"schema_version": 1},
                        ),
                        routing_key=claim.event_type,
                        mandatory=True,
                        timeout=3,
                    )
                    if confirmation is None or confirmation is False:
                        raise RuntimeError("Publisher confirmation was absent.")
        status = await settle_claim(claim)
    except Exception as error:
        # Exception strings from AMQP libraries may contain credential-bearing URLs.
        status = await settle_claim(claim, f"Publisher failure: {type(error).__name__}")
    return {
        "event_id": claim.id,
        "status": status,
        "attempts": claim.attempt,
        "transport_fault": "client_amqp_disconnect" if fault_disconnect else None,
    }


@asynccontextmanager
async def publisher_route(fault_disconnect: bool = False) -> AsyncIterator[str]:
    if not fault_disconnect:
        yield broker_url()
        return

    def disconnect(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.close()

    # The experiment owns this loopback listener. It never probes a port that
    # might belong to another project or stops the shared RabbitMQ process.
    server = await asyncio.start_server(disconnect, host="127.0.0.1", port=0)
    try:
        port = server.sockets[0].getsockname()[1]
        yield f"amqp://guest:guest@127.0.0.1:{port}/"
    finally:
        server.close()
        await server.wait_closed()


async def publish_once(event_id: str, *, fault_disconnect: bool = False) -> dict[str, Any]:
    if fault_disconnect and not get_settings().sandbox_mode:
        raise ValueError("Broker route fault injection requires explicit sandbox mode.")
    claims = await claim_events(1, event_id)
    if not claims:
        return {"event_id": event_id, "status": "NOT_CLAIMED"}
    return await publish_claim(claims[0], fault_disconnect=fault_disconnect)


async def publish_batch(limit: int = 25) -> list[dict[str, Any]]:
    claims = await claim_events(min(limit, 25))
    semaphore = asyncio.Semaphore(5)

    async def publish_bounded(claim: EventClaim) -> dict[str, Any]:
        async with semaphore:
            return await publish_claim(claim)

    results = await asyncio.gather(*(publish_bounded(claim) for claim in claims))
    async with SessionLocal() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(OutboxEvent)
            .where(OutboxEvent.status.in_(["PENDING", "RETRY", "PUBLISHING"]))
        )
        OUTBOX_BACKLOG.set(count or 0)
    return results


def event_owner_ids(payload: dict[str, Any]) -> set[str]:
    result = set()
    for key in ["owner_id", "merchant_owner_id", "destination_owner_id"]:
        value = payload.get(key)
        if isinstance(value, str):
            try:
                result.add(str(UUID(value)))
            except ValueError:
                continue
    return result


async def consume_event(event_id: str) -> dict[str, Any]:
    identifier = str(UUID(event_id))
    async with SessionLocal.begin() as session:
        event = await session.get(OutboxEvent, identifier)
        if event is None:
            raise LookupError("Unknown durable outbox event.")
        inserted = await session.scalar(
            insert(InboxEvent)
            .values(id=new_id(), event_id=identifier, consumer=CONSUMER)
            .on_conflict_do_nothing(constraint="uq_inbox_consumer")
            .returning(InboxEvent.id)
        )
        if inserted is None:
            return {"event_id": identifier, "duplicate": True, "deliveries_created": 0}
        owners = event_owner_ids(event.payload)
        subscriptions = (
            await session.scalars(
                select(WebhookSubscription).where(
                    WebhookSubscription.active.is_(True),
                    WebhookSubscription.receiver == "mock",
                    WebhookSubscription.owner_id.in_(owners),
                )
            )
        ).all()
        created = 0
        for subscription in subscriptions:
            if event.event_type not in subscription.event_types:
                continue
            delivery = await session.scalar(
                insert(WebhookDelivery)
                .values(
                    id=new_id(),
                    event_id=identifier,
                    subscription_id=subscription.id,
                    event_type=event.event_type,
                    status="PENDING",
                    attempts=0,
                )
                .on_conflict_do_nothing(constraint="uq_webhook_event_subscription")
                .returning(WebhookDelivery.id)
            )
            created += int(delivery is not None)
    # Returning only after the context commits is the acknowledgement boundary.
    return {"event_id": identifier, "duplicate": False, "deliveries_created": created}


async def process_message(message: AbstractIncomingMessage, *, acknowledge: bool = True) -> None:
    try:
        body = json.loads(message.body)
        if not isinstance(body, dict) or not isinstance(body.get("event_id"), str):
            raise ValueError("Malformed event envelope.")
        identifier = str(UUID(body["event_id"]))
    except (ValueError, UnicodeDecodeError):
        await message.reject(requeue=False)
        return
    try:
        await consume_event(identifier)
    except LookupError:
        await message.reject(requeue=False)
    except Exception:
        # Keep temporary DB outages recoverable. Closing the connection after this
        # failure adds bounded reconnect backoff instead of a hot requeue loop.
        await message.nack(requeue=True)
        raise
    else:
        if acknowledge:
            await message.ack()


async def consume_broker_batch(limit: int = 25, *, acknowledge: bool = True) -> int:
    if not acknowledge and not get_settings().sandbox_mode:
        raise ValueError("Crash-before-ack injection requires explicit sandbox mode.")
    connection = await aio_pika.connect(broker_url(), timeout=3)
    processed = 0
    async with connection:
        channel = await connection.channel(publisher_confirms=True, on_return_raises=True)
        _, queue = await declare_topology(channel)
        for _ in range(max(1, min(limit, 100))):
            message = await queue.get(timeout=1, fail=False)
            if message is None:
                break
            await process_message(message, acknowledge=acknowledge)
            processed += 1
    # Deliberately unacked messages are redelivered when this channel closes.
    return processed


async def consumer_forever() -> None:
    while True:
        try:
            connection = await aio_pika.connect(broker_url(), timeout=3)
            async with connection:
                channel = await connection.channel(publisher_confirms=True, on_return_raises=True)
                _, queue = await declare_topology(channel)
                async with queue.iterator() as messages:
                    async for message in messages:
                        await process_message(message)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning("consumer.reconnect type=%s", type(error).__name__)
            await asyncio.sleep(2)
