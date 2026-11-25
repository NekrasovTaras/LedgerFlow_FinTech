import asyncio
import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import aio_pika
import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select

from app.core.config import get_settings
from app.infrastructure.database import SessionLocal
from app.infrastructure.models import (
    AuditEvent,
    InboxEvent,
    OutboxEvent,
    User,
    WebhookDelivery,
    WebhookSubscription,
    new_id,
)
from app.modules.outbox import application as outbox
from app.modules.webhooks import application as webhooks


@dataclass(frozen=True)
class EventBundle:
    event_id: str
    owner_id: str
    subscription_id: str


@pytest_asyncio.fixture
async def bundle(migrated_database: None) -> EventBundle:
    async with SessionLocal.begin() as session:
        owner = User(
            email=f"events-{new_id()}@example.test",
            display_name="Event owner",
            password_hash="not-a-login-account",
            role="customer",
            active=True,
        )
        foreign = User(
            email=f"events-{new_id()}@example.test",
            display_name="Other owner",
            password_hash="not-a-login-account",
            role="customer",
            active=True,
        )
        session.add_all([owner, foreign])
        await session.flush()
        subscription = WebhookSubscription(
            owner_id=owner.id, receiver="mock", event_types=["payment.created"], active=True
        )
        session.add_all(
            [
                subscription,
                WebhookSubscription(
                    owner_id=foreign.id,
                    receiver="mock",
                    event_types=["payment.created"],
                    active=True,
                ),
            ]
        )
        event = OutboxEvent(
            event_type="payment.created",
            payload={"owner_id": owner.id},
            status="ISOLATED",
            attempts=0,
        )
        session.add(event)
        await session.flush()
        return EventBundle(event.id, owner.id, subscription.id)


async def delivery_id_for(bundle: EventBundle) -> str:
    await outbox.consume_event(bundle.event_id)
    async with SessionLocal() as session:
        identifier = await session.scalar(
            select(WebhookDelivery.id).where(
                WebhookDelivery.event_id == bundle.event_id,
                WebhookDelivery.subscription_id == bundle.subscription_id,
            )
        )
        assert identifier is not None
        return identifier


def test_owner_scope_ignores_invalid_and_unrecognized_ids() -> None:
    owner = new_id()
    assert outbox.event_owner_ids(
        {"owner_id": owner, "destination_owner_id": "invalid", "unrecognized_owner_id": new_id()}
    ) == {owner}


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/events",
        "https://example.com:9003/events",
        "http://localhost:8000/events",
        "http://localhost:9003/other",
        "http://user:password@localhost:9003/events",
        "http://localhost:9003/events?url=other",
        "http://localhost:9003/events#fragment",
    ],
)
def test_receiver_allowlist_rejects_arbitrary_destinations(monkeypatch, url: str) -> None:
    monkeypatch.setattr(get_settings(), "webhook_receiver_url", url)
    with pytest.raises(ValueError, match="allowlist"):
        webhooks.allowed_receiver_url("mock")


def test_signature_covers_exact_body_timestamp_and_event_identifier() -> None:
    now = datetime.now(UTC)
    claim = webhooks.DeliveryClaim(
        new_id(),
        new_id(),
        "payment.created",
        {"amount_minor": 123},
        now,
        new_id(),
        new_id(),
        "mock",
        now,
        1,
    )
    body, headers = webhooks.signed_body(claim, timestamp=123456)
    material = b"123456." + claim.event_id.encode() + b"." + body
    expected = hmac.new(
        get_settings().webhook_secret.encode(), material, hashlib.sha256
    ).hexdigest()
    assert headers["X-LedgerFlow-Signature"] == "sha256=" + expected
    assert headers["X-LedgerFlow-Event-ID"] == claim.event_id
    assert b'"amount_minor":123' in body


def test_test_database_has_isolated_transport_namespace() -> None:
    assert outbox.transport_suffix().endswith("_test")
    assert outbox.exchange_name() != "ledgerflow.events"
    assert outbox.queue_name() != "ledgerflow.events.consumer"


@pytest.mark.integration
async def test_consumer_deduplicates_concurrent_delivery_and_enforces_owner_scope(bundle) -> None:
    responses = await asyncio.gather(*(outbox.consume_event(bundle.event_id) for _ in range(20)))
    assert sum(response["deliveries_created"] for response in responses) == 1
    assert sum(not response["duplicate"] for response in responses) == 1
    async with SessionLocal() as session:
        deliveries = (
            await session.scalars(
                select(WebhookDelivery).where(WebhookDelivery.event_id == bundle.event_id)
            )
        ).all()
        assert len(deliveries) == 1
        assert deliveries[0].subscription_id == bundle.subscription_id
        assert (
            await session.scalar(
                select(func.count())
                .select_from(InboxEvent)
                .where(
                    InboxEvent.event_id == bundle.event_id, InboxEvent.consumer == outbox.CONSUMER
                )
            )
            == 1
        )


@pytest.mark.integration
async def test_consumer_rolls_back_inbox_if_business_effect_fails(bundle, monkeypatch) -> None:
    real_insert = outbox.insert

    def fail_delivery_insert(model: Any) -> Any:
        if model is WebhookDelivery:
            raise RuntimeError("Injected consumer crash before the effect commits.")
        return real_insert(model)

    with monkeypatch.context() as patch:
        patch.setattr(outbox, "insert", fail_delivery_insert)
        with pytest.raises(RuntimeError, match="Injected consumer crash"):
            await outbox.consume_event(bundle.event_id)
    async with SessionLocal() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(InboxEvent)
                .where(InboxEvent.event_id == bundle.event_id)
            )
            == 0
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(WebhookDelivery)
                .where(WebhookDelivery.event_id == bundle.event_id)
            )
            == 0
        )
    assert (await outbox.consume_event(bundle.event_id))["deliveries_created"] == 1


@pytest.mark.integration
async def test_unknown_event_does_not_create_an_inbox_record(migrated_database) -> None:
    with pytest.raises(LookupError):
        await outbox.consume_event(new_id())


@pytest.mark.integration
async def test_only_one_publisher_claims_event_and_stale_ack_is_rejected(bundle) -> None:
    attempts = await asyncio.gather(*(outbox.claim_events(1, bundle.event_id) for _ in range(10)))
    claims = [claim for group in attempts for claim in group]
    assert len(claims) == 1
    first = claims[0]
    async with SessionLocal.begin() as session:
        event = await session.get(OutboxEvent, bundle.event_id)
        event.locked_until = datetime.now(UTC) - timedelta(seconds=1)
    second = (await outbox.claim_events(1, bundle.event_id))[0]
    assert second.attempt == 2
    assert await outbox.settle_claim(first) == "STALE"
    assert await outbox.settle_claim(second, "Injected second failure") == "ISOLATED"


@pytest.mark.integration
@pytest.mark.parametrize("state", ["PUBLISHING", "ISOLATED_PUBLISHING"])
async def test_expired_final_publisher_lease_becomes_dead(bundle, state: str) -> None:
    async with SessionLocal.begin() as session:
        event = await session.get(OutboxEvent, bundle.event_id)
        event.status, event.attempts = state, outbox.MAX_ATTEMPTS
        event.locked_until = datetime.now(UTC) - timedelta(seconds=1)
    await outbox.claim_events(1, bundle.event_id)
    async with SessionLocal() as session:
        event = await session.get(OutboxEvent, bundle.event_id)
        assert event.status == "DEAD"
        assert event.last_error is not None


@pytest.mark.integration
async def test_actual_amqp_disconnect_persists_event_and_recovers_with_confirm(bundle) -> None:
    failed = await outbox.publish_once(bundle.event_id, fault_disconnect=True)
    assert failed["status"] == "ISOLATED"
    assert failed["transport_fault"] == "client_amqp_disconnect"
    async with SessionLocal() as session:
        event = await session.get(OutboxEvent, bundle.event_id)
        assert event.attempts == 1
        assert event.published_at is None
        assert event.last_error.startswith("Publisher failure:")
    recovered = await outbox.publish_once(bundle.event_id)
    assert recovered["status"] == "PUBLISHED"
    async with SessionLocal() as session:
        event = await session.get(OutboxEvent, bundle.event_id)
        assert event.attempts == 2
        assert event.published_at is not None


@pytest.mark.integration
async def test_real_redelivery_after_commit_before_ack_has_one_business_effect(bundle) -> None:
    assert (await outbox.publish_once(bundle.event_id))["status"] == "PUBLISHED"
    assert await outbox.consume_broker_batch(100, acknowledge=False) >= 1
    assert await outbox.consume_broker_batch(100) >= 1
    async with SessionLocal() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(InboxEvent)
                .where(InboxEvent.event_id == bundle.event_id)
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(WebhookDelivery)
                .where(WebhookDelivery.event_id == bundle.event_id)
            )
            == 1
        )


@pytest.mark.integration
async def test_malformed_broker_message_is_dead_lettered(migrated_database) -> None:
    await outbox.consume_broker_batch(100)
    connection = await aio_pika.connect(outbox.broker_url(), timeout=3)
    async with connection:
        channel = await connection.channel(publisher_confirms=True, on_return_raises=True)
        exchange, _ = await outbox.declare_topology(channel)
        await exchange.publish(aio_pika.Message(b"invalid-json"), routing_key="invalid", timeout=3)
        assert await outbox.consume_broker_batch(1) == 1
        dead_queue = await channel.declare_queue(
            f"ledgerflow.events.dlq{outbox.transport_suffix()}", durable=True
        )
        message = await dead_queue.get(timeout=3)
        assert message.body == b"invalid-json"
        await message.ack()


@pytest.mark.integration
async def test_webhook_http_failure_retries_then_signed_delivery_and_receiver_dedup(bundle) -> None:
    identifier = await delivery_id_for(bundle)
    async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
        control = await client.post("http://localhost:9003/control/fail", json={"fail": True})
        control.raise_for_status()
        try:
            first = (await webhooks.claim_deliveries(1, identifier))[0]
            failed = await webhooks.deliver_claim(first)
            assert failed["http_status"] == 500
            assert failed["status"] == "RETRY"
        finally:
            response = await client.post("http://localhost:9003/control/fail", json={"fail": False})
            response.raise_for_status()
        async with SessionLocal.begin() as session:
            row = await session.get(WebhookDelivery, identifier)
            row.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
        second = (await webhooks.claim_deliveries(1, identifier))[0]
        delivered = await webhooks.deliver_claim(second)
        assert delivered["status"] == "DELIVERED"
        assert delivered["attempts"] == 2
        body, headers = webhooks.signed_body(second)
        duplicate = await client.post(
            webhooks.allowed_receiver_url("mock"), content=body, headers=headers
        )
        assert duplicate.status_code == 200
        assert duplicate.json()["duplicate"] is True
    async with SessionLocal() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "webhook.attempt", AuditEvent.resource_id == identifier)
            )
            == 2
        )


@pytest.mark.integration
async def test_webhook_old_lease_cannot_overwrite_new_attempt(bundle) -> None:
    identifier = await delivery_id_for(bundle)
    first = (await webhooks.claim_deliveries(1, identifier))[0]
    async with SessionLocal.begin() as session:
        delivery = await session.get(WebhookDelivery, identifier)
        delivery.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    second = (await webhooks.claim_deliveries(1, identifier))[0]
    assert await webhooks.settle_delivery(first, 200, None) == "STALE"
    assert await webhooks.settle_delivery(second, 500, "Injected receiver error") == "RETRY"


@pytest.mark.integration
async def test_webhook_exhausted_retries_and_expired_last_attempt_are_dead(bundle) -> None:
    identifier = await delivery_id_for(bundle)
    async with SessionLocal.begin() as session:
        delivery = await session.get(WebhookDelivery, identifier)
        delivery.status, delivery.attempts = "SENDING", webhooks.MAX_ATTEMPTS
        delivery.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    assert await webhooks.claim_deliveries(1, identifier) == []
    async with SessionLocal() as session:
        row = await session.get(WebhookDelivery, identifier)
        assert row.status == "DEAD"


@pytest.mark.integration
async def test_disabled_subscription_never_sends_a_delivery(bundle) -> None:
    identifier = await delivery_id_for(bundle)
    async with SessionLocal.begin() as session:
        subscription = await session.get(WebhookSubscription, bundle.subscription_id)
        subscription.active = False
    assert await webhooks.claim_deliveries(1, identifier) == []
    async with SessionLocal() as session:
        row = await session.get(WebhookDelivery, identifier)
        assert row.status == "CANCELED"
