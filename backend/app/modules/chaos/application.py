import asyncio
import json
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import aio_pika
import httpx
from aio_pika.abc import AbstractChannel, AbstractConnection, AbstractIncomingMessage, AbstractQueue
from redis.asyncio import Redis
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.config import get_settings
from app.core.context import correlation_id
from app.core.errors import AppError
from app.core.security import hash_password
from app.infrastructure.database import SessionLocal, engine
from app.infrastructure.models import (
    ChaosExperiment,
    Discrepancy,
    InboxEvent,
    JournalEntry,
    OutboxEvent,
    TimelineEvent,
    User,
    Wallet,
    WebhookDelivery,
    WebhookSubscription,
    new_id,
)
from app.modules.ledger.application import (
    audit,
    create_wallet,
    idempotent,
    require_roles,
    serialize,
    topup,
    wallet_view,
)
from app.modules.outbox.application import (
    broker_url,
    consume_event,
    declare_topology,
    exchange_name,
    publish_once,
)
from app.modules.payments.application import (
    create_payment,
    create_transfer,
    process_payment,
    resolve_payment,
)
from app.modules.providers.application import provider_url
from app.modules.reconciliation.application import run_reconciliation, verify_invariants
from app.modules.webhooks.application import claim_deliveries, deliver_claim

from .scenarios import SCENARIOS

LOCK_KEY = 644101078
MAX_DURATION_SECONDS = 90
_tasks: dict[str, asyncio.Task[None]] = {}


def authorized(actor: User) -> None:
    require_roles(actor, {"administrator", "operator"})
    if not get_settings().sandbox_mode:
        raise AppError("SANDBOX_DISABLED", "Chaos Lab requires explicit sandbox mode.", 403)


async def _release_lock(connection: AsyncConnection) -> None:
    try:
        await connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": LOCK_KEY})
        await connection.commit()
    except BaseException:
        # A pooled connection must never retain a session advisory lock.
        await connection.invalidate()
        raise
    finally:
        await connection.close()


async def _acquire_lock() -> AsyncConnection | None:
    connection = await engine.connect()
    try:
        locked = await connection.scalar(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": LOCK_KEY}
        )
        await connection.commit()
        if locked:
            return connection
    except BaseException:
        await connection.close()
        raise
    await connection.close()
    return None


async def recover_stale_experiments() -> int:
    connection = await _acquire_lock()
    if connection is None:
        return 0
    try:
        async with SessionLocal.begin() as session:
            rows = (
                await session.scalars(
                    select(ChaosExperiment)
                    .where(
                        ChaosExperiment.status == "RUNNING",
                        ChaosExperiment.created_at < datetime.now(UTC) - timedelta(seconds=120),
                    )
                    .with_for_update()
                )
            ).all()
            for row in rows:
                row.status = "INTERRUPTED"
                row.completed_at = datetime.now(UTC)
                row.result = {"passed": False, "error": "Runner stopped before a terminal result."}
                row.invariants = await verify_invariants(session)
                row.timeline = [
                    *row.timeline,
                    {
                        "at": datetime.now(UTC).isoformat(),
                        "step": "stale_runner_recovered",
                        "status": "INTERRUPTED",
                    },
                ]
            return len(rows)
    finally:
        await _release_lock(connection)


async def start_experiment(actor: User, scenario_id: str) -> dict[str, Any]:
    authorized(actor)
    if scenario_id not in SCENARIOS:
        raise AppError("UNKNOWN_SCENARIO", "Choose a listed Chaos Lab scenario.", 422)
    await recover_stale_experiments()
    connection = await _acquire_lock()
    if connection is None:
        raise AppError("CHAOS_BUSY", "Another bounded experiment is already running.", 409)
    scenario = SCENARIOS[scenario_id]
    identifier = new_id()
    try:
        async with SessionLocal.begin() as session:
            row = ChaosExperiment(
                id=identifier,
                actor_id=actor.id,
                scenario_id=scenario_id,
                status="RUNNING",
                expected_behavior=scenario.expected_behavior,
                recovery=scenario.recovery,
                result={},
                invariants={},
                timeline=[
                    {
                        "at": datetime.now(UTC).isoformat(),
                        "step": "experiment_accepted",
                        "scope": "isolated synthetic actors; project-local dependencies",
                    }
                ],
            )
            session.add(row)
            audit(session, actor.id, "chaos.started", identifier, {"scenario_id": scenario_id})
            await session.flush()
            result = serialize(row)
    except BaseException:
        await _release_lock(connection)
        raise
    task = asyncio.create_task(_run(identifier, scenario_id, connection))
    _tasks[identifier] = task
    task.add_done_callback(lambda completed: _completed_task(identifier, completed))
    # Enter the runner's try/finally before returning an accepted job. This
    # ensures immediate application shutdown cannot cancel an unstarted task
    # while leaving its session advisory lock checked out of the pool.
    await asyncio.sleep(0)
    return result


def _completed_task(identifier: str, task: asyncio.Task[None]) -> None:
    _tasks.pop(identifier, None)
    if not task.cancelled():
        # Consume an exception if final persistence failed during a DB outage.
        # The row remains RUNNING and is recovered as INTERRUPTED after restart.
        task.exception()


async def get_experiment(actor: User, experiment_id: str) -> dict[str, Any]:
    authorized(actor)
    await recover_stale_experiments()
    async with SessionLocal() as session:
        row = await session.get(ChaosExperiment, experiment_id)
        if row is None:
            raise AppError("EXPERIMENT_NOT_FOUND", "Experiment not found.", 404)
        return serialize(row)


async def wait_for_completion(experiment_id: str) -> None:
    task = _tasks.get(experiment_id)
    if task:
        await asyncio.wait_for(asyncio.shield(task), MAX_DURATION_SECONDS + 15)


async def shutdown_experiments() -> None:
    tasks = list(_tasks.values())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


@dataclass
class ExperimentContext:
    id: str
    requester_id: str
    actor: User
    source: Wallet
    destination: Wallet

    async def record(self, step: str, **details: Any) -> None:
        async with SessionLocal.begin() as session:
            row = await session.scalar(
                select(ChaosExperiment).where(ChaosExperiment.id == self.id).with_for_update()
            )
            assert row is not None
            row.timeline = [
                *row.timeline,
                {
                    "at": datetime.now(UTC).isoformat(),
                    "step": step,
                    **details,
                },
            ]

    def payload(self, behavior: str = "success") -> dict[str, Any]:
        return {
            "wallet_id": self.source.id,
            "merchant_wallet_id": self.destination.id,
            "amount_minor": 10_000,
            "description": f"Chaos experiment {self.id}",
            "provider_behavior": behavior,
        }

    async def payment(self, key: str, behavior: str = "success") -> tuple[dict[str, Any], int]:
        payload = self.payload(behavior)
        return await idempotent(
            self.actor,
            f"chaos:{self.id}:payment",
            key,
            payload,
            lambda session: create_payment(session, self.actor, payload, bypass_risk=True),
        )

    async def balance(self) -> dict[str, Any]:
        async with SessionLocal() as session:
            return await wallet_view(session, self.source)

    async def capture_count(self, payment_id: str) -> int:
        async with SessionLocal() as session:
            return int(
                await session.scalar(
                    select(func.count())
                    .select_from(JournalEntry)
                    .where(JournalEntry.reference_id == f"payment:{payment_id}")
                )
                or 0
            )

    async def probe(self) -> tuple[str, str]:
        event_id = new_id()
        event_type = f"chaos.probe.{self.id}"
        async with SessionLocal.begin() as session:
            session.add(
                WebhookSubscription(
                    owner_id=self.actor.id,
                    receiver="mock",
                    event_types=[event_type],
                    active=True,
                )
            )
            session.add(
                OutboxEvent(
                    id=event_id,
                    event_type=event_type,
                    payload={"owner_id": self.actor.id, "experiment_id": self.id},
                    status="ISOLATED",
                    attempts=0,
                )
            )
        return event_id, event_type


async def _setup(identifier: str) -> ExperimentContext:
    # No public password is attached to an experiment identity.
    password_hash = await asyncio.to_thread(hash_password, secrets.token_urlsafe(48))
    async with SessionLocal.begin() as session:
        experiment = await session.get(ChaosExperiment, identifier)
        assert experiment is not None
        requester_id = experiment.actor_id
        actor = User(
            email=f"chaos-{identifier}@sandbox.ledgerflow.invalid",
            display_name="Chaos customer",
            password_hash=password_hash,
            role="customer",
            active=True,
            is_sandbox=True,
        )
        merchant = User(
            email=f"merchant-{identifier}@sandbox.ledgerflow.invalid",
            display_name="Chaos merchant",
            password_hash=password_hash,
            role="merchant",
            active=True,
            is_sandbox=True,
        )
        session.add_all([actor, merchant])
        await session.flush()
        source = await create_wallet(session, actor.id, f"Chaos {identifier[:8]} source")
        destination = await create_wallet(
            session, merchant.id, f"Chaos {identifier[:8]} destination"
        )
        await topup(session, actor, source.id, 100_000)
    context = ExperimentContext(identifier, requester_id, actor, source, destination)
    await context.record(
        "synthetic_wallets_created",
        source_wallet_id=source.id,
        destination_wallet_id=destination.id,
        funded_minor=100_000,
    )
    return context


async def _run(identifier: str, scenario_id: str, connection: AsyncConnection) -> None:
    token = correlation_id.set(f"chaos-{identifier}")
    status = "FAILED"
    result: dict[str, Any] = {"passed": False}
    try:
        async with asyncio.timeout(MAX_DURATION_SECONDS):
            context = await _setup(identifier)
            result = await RUNNERS[scenario_id](context)
    except asyncio.CancelledError:
        status = "INTERRUPTED"
        result = {"passed": False, "error": "Experiment runner was cancelled."}
    except TimeoutError:
        result = {"passed": False, "error": "The bounded 90-second experiment budget expired."}
    except Exception as error:
        # Exception strings can contain SQL parameters or credential-bearing URLs.
        result = {"passed": False, "error": type(error).__name__}
        if isinstance(error, AppError):
            result["error_code"] = error.code
    finally:
        try:
            async with SessionLocal.begin() as session:
                invariants = await verify_invariants(session)
                row = await session.get(ChaosExperiment, identifier)
                assert row is not None
                if status != "INTERRUPTED":
                    status = "PASSED" if result.get("passed") and invariants["valid"] else "FAILED"
                row.status, row.result, row.invariants = status, result, invariants
                row.completed_at = datetime.now(UTC)
                row.timeline = [
                    *row.timeline,
                    {
                        "at": datetime.now(UTC).isoformat(),
                        "step": "experiment_completed",
                        "status": status,
                        "invariants": invariants,
                    },
                ]
                audit(session, row.actor_id, "chaos.completed", identifier, {"status": status})
        finally:
            await _release_lock(connection)
            correlation_id.reset(token)


async def _settle(context: ExperimentContext, payment_id: str) -> dict[str, Any]:
    payment = await process_payment(payment_id, context.actor)
    if payment["status"] == "UNKNOWN":
        payment = await resolve_payment(payment_id)
    return payment


async def provider_unavailable(context: ExperimentContext) -> dict[str, Any]:
    created, status = await context.payment("unavailable", "unavailable")
    if status != 200:
        return {"passed": False, "creation": created, "http_status": status}
    payment = await _settle(context, created["id"])
    async with SessionLocal() as session:
        fallback = (
            await session.scalar(
                select(func.count())
                .select_from(TimelineEvent)
                .where(
                    TimelineEvent.payment_id == payment["id"],
                    TimelineEvent.kind == "provider.fallback",
                )
            )
            or 0
        )
    captures = await context.capture_count(payment["id"])
    await context.record(
        "confirmed_no_effect_fallback",
        payment_id=payment["id"],
        status=payment["status"],
        fallback_events=fallback,
        captures=captures,
    )
    return {
        "passed": payment["status"] == "SUCCEEDED" and fallback == 1 and captures == 1,
        "payment": payment,
        "fallback_events": fallback,
        "capture_journals": captures,
    }


async def provider_lost_response(context: ExperimentContext) -> dict[str, Any]:
    created, status = await context.payment("lost-response", "lost_response")
    if status != 200:
        return {"passed": False, "creation": created, "http_status": status}
    unknown = await process_payment(created["id"], context.actor)
    held = await context.balance()
    await context.record(
        "provider_response_lost",
        payment_id=created["id"],
        status=unknown["status"],
        reserved_minor=held["reserved_minor"],
    )
    resolved = await resolve_payment(created["id"])
    repeated = await resolve_payment(created["id"])
    captures = await context.capture_count(created["id"])
    async with SessionLocal() as session:
        fallbacks = int(
            await session.scalar(
                select(func.count())
                .select_from(TimelineEvent)
                .where(
                    TimelineEvent.payment_id == created["id"],
                    TimelineEvent.kind == "provider.fallback",
                )
            )
            or 0
        )
    balance = await context.balance()
    await context.record(
        "original_provider_status_resolved",
        status=resolved["status"],
        captures=captures,
        available_minor=balance["available_minor"],
    )
    return {
        "passed": unknown["status"] == "UNKNOWN"
        and held["reserved_minor"] == 10_000
        and resolved["status"] == repeated["status"] == "SUCCEEDED"
        and captures == 1
        and balance["posted_minor"] == 90_000
        and balance["reserved_minor"] == 0
        and fallbacks == 0
        and unknown["provider"] == resolved["provider"],
        "initial_status": unknown["status"],
        "resolved_payment": resolved,
        "capture_journals": captures,
        "balance": balance,
        "fallback_performed": bool(fallbacks),
        "fallback_events": fallbacks,
    }


async def broker_outage(context: ExperimentContext) -> dict[str, Any]:
    event_id, event_type = await context.probe()
    async with evidence_queue(context, event_type) as (_, _, queue):
        failed = await publish_once(event_id, fault_disconnect=True)
        async with SessionLocal() as session:
            row = await session.get(OutboxEvent, event_id)
            assert row is not None
            persisted_failure = (
                row.status == "ISOLATED" and row.attempts == 1 and bool(row.last_error)
            )
        await context.record(
            "actual_amqp_route_disconnected",
            event_id=event_id,
            outcome=failed,
            durable_failure=persisted_failure,
        )
        restored = await publish_once(event_id)
        message = await _message(queue)
        assert json.loads(message.body)["event_id"] == event_id
        effect = await consume_event(event_id)
        await message.ack()
        counts = await effect_counts(event_id)
    await context.record(
        "actual_broker_confirm_and_delivery_received", outcome=restored, consumer=effect, **counts
    )
    return {
        "passed": persisted_failure
        and restored["status"] == "PUBLISHED"
        and counts == {"inbox_records": 1, "webhook_deliveries": 1},
        "event_id": event_id,
        "failed_attempt": failed,
        "recovered_attempt": restored,
        **counts,
        "fault_scope": "experiment-owned client TCP route; broker process remains running",
    }


@asynccontextmanager
async def evidence_queue(
    context: ExperimentContext,
    event_type: str,
) -> AsyncIterator[tuple[AbstractConnection, AbstractChannel, AbstractQueue]]:
    connection = await aio_pika.connect(broker_url(), timeout=8)
    channel = await connection.channel(publisher_confirms=True, on_return_raises=True)
    exchange, _ = await declare_topology(channel)
    name = f"ledgerflow.chaos.{context.id}"
    queue = await channel.declare_queue(name, durable=False, arguments={"x-expires": 120_000})
    await queue.bind(exchange, event_type)
    try:
        yield connection, channel, queue
    finally:
        if not connection.is_closed:
            await queue.delete(if_unused=False, if_empty=False)
            await connection.close()
        else:
            cleanup = await aio_pika.connect(broker_url(), timeout=8)
            async with cleanup:
                cleanup_channel = await cleanup.channel()
                owned = await cleanup_channel.declare_queue(
                    name,
                    durable=False,
                    arguments={"x-expires": 120_000},
                )
                await owned.delete(if_unused=False, if_empty=False)


async def _message(queue: AbstractQueue) -> AbstractIncomingMessage:
    message = await queue.get(timeout=5, fail=True)
    assert message is not None
    return message


async def effect_counts(event_id: str) -> dict[str, int]:
    async with SessionLocal() as session:
        return {
            "inbox_records": int(
                await session.scalar(
                    select(func.count())
                    .select_from(InboxEvent)
                    .where(InboxEvent.event_id == event_id)
                )
                or 0
            ),
            "webhook_deliveries": int(
                await session.scalar(
                    select(func.count())
                    .select_from(WebhookDelivery)
                    .where(WebhookDelivery.event_id == event_id)
                )
                or 0
            ),
        }


async def worker_before_ack(context: ExperimentContext) -> dict[str, Any]:
    event_id, event_type = await context.probe()
    async with evidence_queue(context, event_type) as (connection, _, queue):
        publication = await publish_once(event_id)
        message = await _message(queue)
        assert json.loads(message.body)["event_id"] == event_id
        first = await consume_event(event_id)
        await context.record(
            "business_transaction_committed_before_ack",
            event_id=event_id,
            outcome=first,
            acknowledgement_sent=False,
        )
        await connection.close()
        recovered_connection = await aio_pika.connect(broker_url(), timeout=8)
        async with recovered_connection:
            channel = await recovered_connection.channel()
            redelivery_queue = await channel.declare_queue(
                queue.name,
                durable=False,
                arguments={"x-expires": 120_000},
            )
            redelivered = await _message(redelivery_queue)
            redelivered_flag = redelivered.redelivered
            second = await consume_event(event_id)
            await redelivered.ack()
        counts = await effect_counts(event_id)
    await context.record(
        "actual_unacked_redelivery",
        redelivered=redelivered_flag,
        duplicate=second["duplicate"],
        **counts,
    )
    return {
        "passed": publication["status"] == "PUBLISHED"
        and redelivered_flag
        and second["duplicate"]
        and counts == {"inbox_records": 1, "webhook_deliveries": 1},
        "event_id": event_id,
        "redelivered": redelivered_flag,
        "first_processing": first,
        "redelivery_processing": second,
        **counts,
        "fault_scope": "consumer connection closed after commit; no OS process killed",
    }


async def duplicate_event(context: ExperimentContext) -> dict[str, Any]:
    event_id, event_type = await context.probe()
    async with evidence_queue(context, event_type) as (_, channel, queue):
        publication = await publish_once(event_id)
        first_message = await _message(queue)
        envelope = first_message.body
        first = await consume_event(event_id)
        await first_message.ack()
        exchange = await channel.get_exchange(exchange_name())
        confirmed = await exchange.publish(
            aio_pika.Message(
                envelope,
                message_id=event_id,
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            ),
            routing_key=event_type,
            mandatory=True,
            timeout=5,
        )
        duplicate = await _message(queue)
        assert duplicate.body == envelope
        second = await consume_event(event_id)
        await duplicate.ack()
        counts = await effect_counts(event_id)
    await context.record(
        "duplicate_envelope_actually_delivered", messages=2, duplicate=second["duplicate"], **counts
    )
    return {
        "passed": publication["status"] == "PUBLISHED"
        and bool(confirmed)
        and second["duplicate"]
        and counts == {"inbox_records": 1, "webhook_deliveries": 1},
        "event_id": event_id,
        "broker_messages": 2,
        "first_processing": first,
        "second_processing": second,
        **counts,
    }


async def duplicate_requests(context: ExperimentContext) -> dict[str, Any]:
    responses = await asyncio.gather(*(context.payment("one-key") for _ in range(100)))
    identifiers = {body.get("id") for body, status in responses if status == 200}
    accepted = sum(status == 200 for _, status in responses)
    if len(identifiers) != 1 or accepted != 100:
        return {"passed": False, "accepted": accepted, "unique_payments": len(identifiers)}
    payment_id = next(iter(identifiers))
    assert isinstance(payment_id, str)
    payment = await _settle(context, payment_id)
    captures = await context.capture_count(payment_id)
    balance = await context.balance()
    await context.record(
        "one_hundred_idempotent_commands_completed",
        accepted=accepted,
        unique_payments=len(identifiers),
        captures=captures,
    )
    return {
        "passed": payment["status"] == "SUCCEEDED"
        and captures == 1
        and balance["available_minor"] == 90_000,
        "entrypoint": "application",
        "commands": 100,
        "accepted": accepted,
        "unique_payments": len(identifiers),
        "payment_id": payment_id,
        "capture_journals": captures,
        "balance": balance,
    }


async def concurrent_debits(context: ExperimentContext) -> dict[str, Any]:
    responses = await asyncio.gather(*(context.payment(f"debit-{index}") for index in range(20)))
    accepted = [body for body, status in responses if status == 200]
    insufficient = sum(
        status == 409 and body.get("error", {}).get("code") == "INSUFFICIENT_FUNDS"
        for body, status in responses
    )
    captures = []
    for payment in accepted:
        captures.append(await _settle(context, payment["id"]))
    balance = await context.balance()
    succeeded = sum(payment["status"] == "SUCCEEDED" for payment in captures)
    await context.record(
        "concurrent_debits_settled",
        accepted=len(accepted),
        insufficient_funds=insufficient,
        captured=succeeded,
        available_minor=balance["available_minor"],
    )
    return {
        "passed": len(accepted) == insufficient == succeeded == 10
        and balance["posted_minor"] == balance["available_minor"] == balance["reserved_minor"] == 0,
        "entrypoint": "application",
        "commands": 20,
        "accepted": len(accepted),
        "insufficient_funds": insufficient,
        "captured": succeeded,
        "balance": balance,
        "additional_risk_rules": "explicitly bypassed only for isolated experiment commands",
    }


def receiver_control_url() -> str:
    parsed = urlsplit(get_settings().webhook_receiver_url)
    # Only the fixed trusted configuration is used; no request-provided URL exists.
    if parsed.hostname not in {"127.0.0.1", "localhost", "receiver", "webhook-receiver"}:
        raise AppError("INVALID_SANDBOX_RECEIVER", "Receiver is outside the local mock scope.", 409)
    return urlunsplit((parsed.scheme, parsed.netloc, "/control/fail", "", ""))


async def webhook_failure(context: ExperimentContext) -> dict[str, Any]:
    event_id, _ = await context.probe()
    control_url = receiver_control_url()
    async with httpx.AsyncClient(timeout=3, trust_env=False, follow_redirects=False) as client:
        original_response = await client.get(control_url)
        original_response.raise_for_status()
        original = original_response.json()
        if original.get("fail") is not False:
            raise AppError(
                "DEPENDENCY_ALREADY_FAULTED",
                "Restore the receiver before running this experiment.",
                409,
            )
        try:
            enabled = await client.post(control_url, json={"fail": True})
            enabled.raise_for_status()
            await consume_event(event_id)
            async with SessionLocal() as session:
                delivery = await session.scalar(
                    select(WebhookDelivery).where(WebhookDelivery.event_id == event_id)
                )
                assert delivery is not None
                delivery_id = delivery.id
            claims = await claim_deliveries(1, delivery_id)
            first = await deliver_claim(claims[0]) if claims else {"status": "CLAIMED_BY_WORKER"}
            for _ in range(25):
                async with SessionLocal() as session:
                    current = await session.get(WebhookDelivery, delivery_id)
                    assert current is not None
                    if current.status in {"RETRY", "DEAD"}:
                        failed_attempts = current.attempts
                        failure = current.last_error or ""
                        break
                await asyncio.sleep(0.1)
            else:
                raise RuntimeError("No failed webhook attempt was observed.")
            await context.record(
                "actual_receiver_500_recorded",
                delivery_id=delivery_id,
                attempts=failed_attempts,
                error=failure,
                outcome=first,
            )
        finally:
            restored = await client.post(control_url, json=original)
            restored.raise_for_status()
    final_status = "UNKNOWN"
    final_attempts = 0
    for _ in range(35):
        claims = await claim_deliveries(1, delivery_id)
        if claims:
            await deliver_claim(claims[0])
        async with SessionLocal() as session:
            current = await session.get(WebhookDelivery, delivery_id)
            assert current is not None
            final_status, final_attempts = current.status, current.attempts
        if final_status == "DELIVERED":
            break
        await asyncio.sleep(0.2)
    await context.record(
        "signed_webhook_retry_completed",
        status=final_status,
        attempts=final_attempts,
        delivery_id=delivery_id,
    )
    return {
        "passed": "HTTP 500" in failure
        and final_status == "DELIVERED"
        and final_attempts > failed_attempts,
        "event_id": event_id,
        "delivery_id": delivery_id,
        "first_failure": failure,
        "status": final_status,
        "attempts": final_attempts,
    }


async def reconciliation_discrepancy(context: ExperimentContext) -> dict[str, Any]:
    created, status = await context.payment("report-discrepancy")
    if status != 200:
        return {"passed": False, "creation": created, "http_status": status}
    payment = await _settle(context, created["id"])
    if payment["status"] != "SUCCEEDED" or not payment["provider"]:
        return {"passed": False, "payment": payment}
    url = f"{provider_url(payment['provider'])}/control"
    async with httpx.AsyncClient(timeout=3, trust_env=False, follow_redirects=False) as client:
        original_response = await client.get(url)
        original_response.raise_for_status()
        original = original_response.json()
        if "report_operation_id" not in original:
            raise RuntimeError("The local mock requires targeted report discrepancy support.")
        try:
            altered = await client.post(
                url,
                json={
                    **original,
                    "report_operation_id": payment["id"],
                    "report_discrepancy_minor": 1,
                },
            )
            altered.raise_for_status()
            balance_before = await context.balance()
            run = await run_reconciliation(context.requester_id)
            async with SessionLocal() as session:
                discrepancies = (
                    await session.scalars(
                        select(Discrepancy).where(
                            Discrepancy.run_id == run["id"],
                            Discrepancy.reference_id == payment["id"],
                            Discrepancy.classification == "PROVIDER_AMOUNT",
                        )
                    )
                ).all()
            await context.record(
                "targeted_report_discrepancy_detected",
                payment_id=payment["id"],
                reconciliation_run_id=run["id"],
                matches=len(discrepancies),
            )
        finally:
            restored = await client.post(url, json=original)
            restored.raise_for_status()
    return {
        "passed": len(discrepancies) == 1,
        "payment_id": payment["id"],
        "reconciliation_run_id": run["id"],
        "detected_discrepancies": len(discrepancies),
        "ledger_balance_unchanged": balance_before["posted_minor"]
        == (await context.balance())["posted_minor"],
        "export_path": f"/api/v1/reconciliation/runs/{run['id']}/export",
    }


async def redis_outage(context: ExperimentContext) -> dict[str, Any]:
    async with Redis.from_url(
        get_settings().redis_url, socket_connect_timeout=1, socket_timeout=1
    ) as cache:
        initial_ping = await cache.ping()

    def disconnect(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.close()

    server = await asyncio.start_server(disconnect, host="127.0.0.1", port=0)
    observed_error = ""
    try:
        port = server.sockets[0].getsockname()[1]
        async with Redis(
            host="127.0.0.1", port=port, socket_connect_timeout=0.5, socket_timeout=0.5
        ) as broken:
            try:
                async with asyncio.timeout(2):
                    await broken.ping()
            except Exception as error:
                observed_error = type(error).__name__
        response, status = await idempotent(
            context.actor,
            f"chaos:{context.id}:redis-transfer",
            "one-transfer",
            {"amount_minor": 10_000},
            lambda session: create_transfer(
                session, context.actor, context.source.id, context.destination.id, 10_000
            ),
        )
    finally:
        server.close()
        await server.wait_closed()
    async with Redis.from_url(
        get_settings().redis_url, socket_connect_timeout=1, socket_timeout=1
    ) as cache:
        recovered_ping = await cache.ping()
    balance = await context.balance()
    await context.record(
        "redis_client_route_failed_ledger_succeeded",
        error=observed_error,
        transfer_status=status,
        recovered_ping=recovered_ping,
    )
    return {
        "passed": bool(initial_ping and recovered_ping and observed_error)
        and status == 200
        and balance["available_minor"] == 90_000,
        "fault_scope": "experiment-owned Redis client TCP route; Redis process remains running",
        "observed_error": observed_error,
        "transfer": response,
        "http_status": status,
        "redis_recovered": bool(recovered_ping),
        "balance": balance,
    }


RUNNERS = {
    "provider_unavailable": provider_unavailable,
    "provider_lost_response": provider_lost_response,
    "broker_outage": broker_outage,
    "worker_before_ack": worker_before_ack,
    "duplicate_event": duplicate_event,
    "duplicate_requests": duplicate_requests,
    "concurrent_debits": concurrent_debits,
    "webhook_failure": webhook_failure,
    "reconciliation_discrepancy": reconciliation_discrepancy,
    "redis_outage": redis_outage,
}
