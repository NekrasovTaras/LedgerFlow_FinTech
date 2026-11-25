"""Payment lifecycle and recovery checks using real PostgreSQL and local providers."""

import asyncio
from typing import Any
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select
from test_financial_acceptance import (
    FinancialEnvironment,
    balances,
    make_environment,
    payment_payload,
)
from test_financial_acceptance import financial_env as financial_env

from app.core.errors import AppError
from app.infrastructure.database import SessionLocal
from app.infrastructure.models import Hold, JournalEntry, Payment, Refund, TimelineEvent
from app.modules.ledger.application import idempotent, wallet_view
from app.modules.payments.application import (
    cancel_payment,
    create_payment,
    process_payment,
    refund_payment,
    resolve_payment,
)
from app.modules.payments.domain import risk_decision, validate_transition
from app.modules.providers.application import provider_url

pytestmark = pytest.mark.integration


async def authorized_payment(
    env: FinancialEnvironment, *, amount: int = 10_000, behavior: str = "success"
) -> dict[str, Any]:
    async with SessionLocal.begin() as session:
        return await create_payment(
            session, env.actor, payment_payload(env, amount, provider_behavior=behavior)
        )


async def payment_journal_count(payment_id: str) -> int:
    async with SessionLocal() as session:
        return (
            await session.scalar(
                select(func.count())
                .select_from(JournalEntry)
                .where(JournalEntry.reference_id == f"payment:{payment_id}")
            )
            or 0
        )


async def test_authorization_and_repeated_processing_capture_exactly_once(
    financial_env: FinancialEnvironment,
) -> None:
    env = financial_env
    payment = await authorized_payment(env)
    assert payment["status"] == "AUTHORIZED"
    source, _, merchant = await balances(env)
    assert (source["available_minor"], source["reserved_minor"], merchant["posted_minor"]) == (
        90_000,
        10_000,
        0,
    )
    completed = await process_payment(payment["id"], env.actor)
    assert completed["status"] == "SUCCEEDED"
    assert (await process_payment(payment["id"], env.actor))["status"] == "SUCCEEDED"
    assert await payment_journal_count(payment["id"]) == 1
    source, _, merchant = await balances(env)
    assert (source["posted_minor"], source["reserved_minor"], merchant["posted_minor"]) == (
        90_000,
        0,
        10_000,
    )


async def test_provider_decline_releases_hold_without_journal(
    financial_env: FinancialEnvironment,
) -> None:
    env = financial_env
    payment = await authorized_payment(env, behavior="decline")
    completed = await process_payment(payment["id"], env.actor)
    assert completed["status"] == "FAILED"
    assert await payment_journal_count(payment["id"]) == 0
    source, _, merchant = await balances(env)
    assert (source["available_minor"], source["reserved_minor"], merchant["posted_minor"]) == (
        100_000,
        0,
        0,
    )


async def test_cancel_authorization_releases_only_the_reservation(
    financial_env: FinancialEnvironment,
) -> None:
    env = financial_env
    payment = await authorized_payment(env)
    async with SessionLocal.begin() as session:
        canceled = await cancel_payment(session, env.actor, payment["id"])
    assert canceled["status"] == "CANCELED"
    async with SessionLocal.begin() as session:
        assert (await cancel_payment(session, env.actor, payment["id"]))["status"] == "CANCELED"
    assert (await process_payment(payment["id"], env.actor))["status"] == "CANCELED"
    assert await payment_journal_count(payment["id"]) == 0
    source, _, merchant = await balances(env)
    assert (source["available_minor"], source["reserved_minor"], merchant["posted_minor"]) == (
        100_000,
        0,
        0,
    )


@pytest.mark.concurrency
async def test_parallel_refunds_cannot_exceed_captured_amount(
    financial_env: FinancialEnvironment,
) -> None:
    env = financial_env
    payment = await authorized_payment(env)
    assert (await process_payment(payment["id"], env.actor))["status"] == "SUCCEEDED"

    async def refund(index: int) -> tuple[dict, int]:
        return await idempotent(
            env.actor,
            f"acceptance-refund:{payment['id']}",
            str(index),
            {"amount_minor": 1_000},
            lambda session: refund_payment(session, env.actor, payment["id"], 1_000),
        )

    outcomes = await asyncio.gather(*(refund(index) for index in range(20)))
    assert sum(status == 200 for _, status in outcomes) == 10
    assert sum(status == 409 for _, status in outcomes) == 10
    async with SessionLocal() as session:
        row = await session.get(Payment, payment["id"])
        assert row is not None and row.status == "REFUNDED" and row.refunded_minor == 10_000
        assert (
            await session.scalar(
                select(func.sum(Refund.amount_minor)).where(Refund.payment_id == payment["id"])
            )
            == 10_000
        )
        assert (
            await session.scalar(
                select(func.count()).select_from(Refund).where(Refund.payment_id == payment["id"])
            )
            == 10
        )
    source, _, merchant = await balances(env)
    assert (source["available_minor"], merchant["available_minor"]) == (100_000, 0)


async def test_partial_refund_idempotency_and_remaining_amount(
    financial_env: FinancialEnvironment,
) -> None:
    env = financial_env
    payment = await authorized_payment(env)
    assert (await process_payment(payment["id"], env.actor))["status"] == "SUCCEEDED"
    key = uuid4().hex

    async def action(session):
        return await refund_payment(session, env.actor, payment["id"], 4_000)

    first = await idempotent(env.actor, "partial-refund", key, {"amount_minor": 4_000}, action)
    replay = await idempotent(env.actor, "partial-refund", key, {"amount_minor": 4_000}, action)
    assert first == replay
    assert first[0]["status"] == "PARTIALLY_REFUNDED" and first[0]["refunded_minor"] == 4_000
    with pytest.raises(AppError) as exceeded:
        async with SessionLocal.begin() as session:
            await refund_payment(session, env.actor, payment["id"], 6_001)
    assert exceeded.value.code == "REFUND_LIMIT"
    async with SessionLocal.begin() as session:
        completed = await refund_payment(session, env.actor, payment["id"], 6_000)
    assert completed["status"] == "REFUNDED" and completed["refunded_minor"] == 10_000


@pytest.mark.concurrency
async def test_cancel_and_capture_race_has_one_consistent_terminal_effect(
    financial_env: FinancialEnvironment,
) -> None:
    env = financial_env
    payment = await authorized_payment(env)

    async def cancel() -> dict:
        async with SessionLocal.begin() as session:
            return await cancel_payment(session, env.actor, payment["id"])

    outcomes = await asyncio.gather(
        process_payment(payment["id"], env.actor), cancel(), return_exceptions=True
    )
    errors = [result for result in outcomes if isinstance(result, Exception)]
    assert all(isinstance(error, AppError) and error.status == 409 for error in errors)
    async with SessionLocal() as session:
        row = await session.get(Payment, payment["id"])
        assert row is not None and row.status in {"CANCELED", "SUCCEEDED"}
        hold = await session.get(Hold, row.hold_id)
        assert hold is not None
        settled = row.status == "SUCCEEDED"
        assert hold.status == ("CAPTURED" if settled else "RELEASED")
    assert await payment_journal_count(payment["id"]) == int(settled)
    source, _, merchant = await balances(env)
    assert source["reserved_minor"] == 0
    assert source["available_minor"] == (90_000 if settled else 100_000)
    assert merchant["available_minor"] == (10_000 if settled else 0)


async def test_lost_provider_response_retains_hold_and_reconciles_once(
    financial_env: FinancialEnvironment,
) -> None:
    env = financial_env
    payment = await authorized_payment(env, behavior="lost_response")
    unknown = await process_payment(payment["id"], env.actor)
    assert unknown["status"] == "UNKNOWN"
    original_provider = unknown["provider"]
    other_provider = "beta" if original_provider == "alpha" else "alpha"
    async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
        receipt = await client.get(f"{provider_url(original_provider)}/payments/{payment['id']}")
        alternate = await client.get(f"{provider_url(other_provider)}/payments/{payment['id']}")
    assert receipt.json()["status"] == "SUCCEEDED"
    assert alternate.json()["status"] == "NOT_FOUND"
    assert await payment_journal_count(payment["id"]) == 0
    source, _, merchant = await balances(env)
    assert (source["posted_minor"], source["reserved_minor"], merchant["posted_minor"]) == (
        100_000,
        10_000,
        0,
    )
    with pytest.raises(AppError) as cancellation:
        async with SessionLocal.begin() as session:
            await cancel_payment(session, env.actor, payment["id"])
    assert cancellation.value.code == "INVALID_PAYMENT_STATE"
    assert (await process_payment(payment["id"], env.actor))["status"] == "UNKNOWN"
    assert (await resolve_payment(payment["id"]))["status"] == "SUCCEEDED"
    assert (await resolve_payment(payment["id"]))["status"] == "SUCCEEDED"
    assert await payment_journal_count(payment["id"]) == 1
    async with SessionLocal() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(TimelineEvent)
                .where(
                    TimelineEvent.payment_id == payment["id"],
                    TimelineEvent.kind == "provider.request",
                )
            )
            == 1
        )
    source, _, merchant = await balances(env)
    assert (source["available_minor"], source["reserved_minor"], merchant["available_minor"]) == (
        90_000,
        0,
        10_000,
    )


async def test_fallback_requires_confirmed_no_effect(
    financial_env: FinancialEnvironment,
) -> None:
    env = financial_env
    payment = await authorized_payment(env, behavior="unavailable")
    result = await process_payment(payment["id"], env.actor)
    assert result["status"] == "SUCCEEDED"
    async with SessionLocal() as session:
        attempts = (
            await session.scalars(
                select(TimelineEvent).where(
                    TimelineEvent.payment_id == payment["id"],
                    TimelineEvent.kind.in_(["provider.request", "provider.fallback"]),
                )
            )
        ).all()
    assert {row.kind for row in attempts} == {"provider.request", "provider.fallback"}
    assert await payment_journal_count(payment["id"]) == 1


async def test_risk_decline_never_reserves_or_moves_funds(
    financial_env: FinancialEnvironment,
) -> None:
    env = financial_env
    async with SessionLocal.begin() as session:
        payment = await create_payment(session, env.actor, payment_payload(env, 1_000_000))
    assert payment["status"] == "FAILED" and payment["risk_decision"] == "DECLINE"
    source, _, merchant = await balances(env)
    assert (source["available_minor"], source["reserved_minor"], merchant["posted_minor"]) == (
        100_000,
        0,
        0,
    )


async def test_review_reserves_without_submitting_to_provider(migrated_database: None) -> None:
    env = await make_environment(500_000)
    async with SessionLocal.begin() as session:
        payment = await create_payment(session, env.actor, payment_payload(env, 200_000))
    assert payment["status"] == "REQUIRES_REVIEW" and payment["risk_decision"] == "REVIEW"
    assert payment["provider"] is None
    async with SessionLocal() as session:
        source = await wallet_view(session, env.source)
    assert (source["available_minor"], source["reserved_minor"]) == (300_000, 200_000)


@pytest.mark.parametrize(
    "current,target",
    [
        ("PROCESSING", "CANCELED"),
        ("UNKNOWN", "CANCELED"),
        ("SUCCEEDED", "AUTHORIZED"),
        ("REFUNDED", "PROCESSING"),
        ("FAILED", "SUCCEEDED"),
    ],
)
def test_invalid_payment_transitions_are_rejected(current: str, target: str) -> None:
    with pytest.raises(AppError) as rejected:
        validate_transition(current, target)
    assert rejected.value.code == "INVALID_PAYMENT_STATE"


@pytest.mark.parametrize(
    "count,failures,new_recipient,rule",
    [
        (100, 0, False, "velocity"),
        (0, 5, False, "consecutive_failures"),
        (0, 0, True, "new_recipient_unusual_amount"),
    ],
)
def test_risk_rules_explain_their_deterministic_decision(
    count: int, failures: int, new_recipient: bool, rule: str
) -> None:
    decision = risk_decision(100_000, count, failures, new_recipient, 200_000, 1_000_000, 100)
    assert rule in decision["rules"]
    assert 0 <= decision["score"] <= 100 and rule in decision["explanation"]
    assert decision["rules_version"] == "2026-11-v1"
