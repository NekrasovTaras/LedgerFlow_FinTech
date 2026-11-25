from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError
from app.infrastructure.database import SessionLocal
from app.infrastructure.models import Payment, Refund, RiskReview, Transfer, User, new_id
from app.modules.ledger.application import (
    FINANCIAL_READERS,
    audit,
    event,
    finish_hold,
    lock_accounts,
    post_journal,
    reserve,
    serialize,
    timeline,
    wallet_for,
)
from app.modules.ledger.domain import PostingInstruction, Side, validate_amount
from app.modules.payments.domain import risk_decision, validate_transition
from app.modules.providers.application import choose_provider, lookup, submit


def payment_view(payment: Payment) -> dict[str, Any]:
    return serialize(payment, {"provider_behavior", "processing_started_at"})


async def payment_for(
    session: AsyncSession, payment_id: str, actor: User | None = None, *, lock: bool = False
) -> Payment:
    statement = select(Payment).where(Payment.id == payment_id)
    payment = await session.scalar(statement.with_for_update() if lock else statement)
    if payment is None:
        raise AppError("PAYMENT_NOT_FOUND", "Payment not found.", 404)
    if actor and actor.role not in FINANCIAL_READERS:
        source = await wallet_for(session, payment.wallet_id)
        destination = await wallet_for(session, payment.merchant_wallet_id)
        if actor.id not in {source.owner_id, destination.owner_id}:
            raise AppError("PAYMENT_NOT_FOUND", "Payment not found.", 404)
    return payment


def transition(session: AsyncSession, payment: Payment, target: str, detail: str = "") -> None:
    validate_transition(payment.status, target)
    previous = payment.status
    payment.status = target
    payment.version += 1
    timeline(session, payment.id, "payment.state", f"{previous} → {target}", detail, target)


async def create_transfer(
    session: AsyncSession,
    actor: User,
    source_wallet_id: str,
    destination_wallet_id: str,
    amount_minor: int,
    description: str = "",
) -> dict[str, Any]:
    validate_amount(amount_minor)
    if source_wallet_id == destination_wallet_id:
        raise AppError("SAME_WALLET", "Source and destination must differ.", 422)
    source = await wallet_for(session, source_wallet_id, actor, spending=True)
    destination = await wallet_for(session, destination_wallet_id, spending=True)
    if source.currency != destination.currency:
        raise AppError("CURRENCY_MISMATCH", "Wallet currencies differ.", 422)
    balances = await lock_accounts(session, [source.account_id, destination.account_id])
    balance = balances[source.account_id]
    if balance.balance_minor - balance.reserved_minor < amount_minor:
        raise AppError("INSUFFICIENT_FUNDS", "Insufficient available balance.", 409)
    transfer_id = new_id()
    journal = await post_journal(
        session,
        "TRANSFER",
        f"transfer:{transfer_id}",
        [
            PostingInstruction(source.account_id, Side.CREDIT, amount_minor),
            PostingInstruction(destination.account_id, Side.DEBIT, amount_minor),
        ],
        description,
    )
    transfer = Transfer(
        id=transfer_id,
        source_wallet_id=source.id,
        destination_wallet_id=destination.id,
        amount_minor=amount_minor,
        currency="RUB",
        description=description,
        journal_id=journal.id,
    )
    session.add(transfer)
    event(
        session,
        "wallet.balance_changed",
        {
            "transfer_id": transfer_id,
            "owner_id": source.owner_id,
            "destination_owner_id": destination.owner_id,
        },
    )
    audit(session, actor.id, "transfer.created", transfer_id, {"amount_minor": amount_minor})
    await session.flush()
    return serialize(transfer)


async def create_payment(
    session: AsyncSession, actor: User, payload: dict[str, Any], *, bypass_risk: bool = False
) -> dict[str, Any]:
    validate_amount(payload["amount_minor"])
    source = await wallet_for(session, payload["wallet_id"], actor, spending=True)
    merchant = await wallet_for(session, payload["merchant_wallet_id"], spending=True)
    merchant_user = await session.get(User, merchant.owner_id)
    if source.id == merchant.id or merchant_user is None or merchant_user.role != "merchant":
        raise AppError("INVALID_MERCHANT", "Choose a published merchant wallet.", 422)
    behavior = payload.get("provider_behavior", "success")
    if behavior != "success" and not get_settings().sandbox_mode:
        raise AppError("SANDBOX_DISABLED", "Provider fault behavior requires sandbox mode.", 403)
    await lock_accounts(session, [source.account_id])
    settings = get_settings()
    cutoff = datetime.now(UTC) - timedelta(minutes=1)
    recent = (
        await session.scalar(
            select(func.count())
            .select_from(Payment)
            .where(Payment.wallet_id == source.id, Payment.created_at >= cutoff)
        )
        or 0
    )
    failures = (
        await session.scalar(
            select(func.count())
            .select_from(Payment)
            .where(
                Payment.wallet_id == source.id,
                Payment.created_at >= cutoff,
                Payment.status == "FAILED",
            )
        )
        or 0
    )
    exists = await session.scalar(
        select(Payment.id)
        .where(Payment.wallet_id == source.id, Payment.merchant_wallet_id == merchant.id)
        .limit(1)
    )
    risk = risk_decision(
        payload["amount_minor"],
        recent,
        failures,
        not bool(exists),
        settings.risk_review_threshold_minor,
        settings.risk_decline_threshold_minor,
        settings.risk_velocity_limit,
    )
    if bypass_risk:
        risk = {
            "score": 0,
            "rules": [],
            "decision": "ALLOW",
            "explanation": "Isolated concurrency experiment; risk rules explicitly bypassed.",
            "rules_version": "sandbox-experiment",
        }
    payment = Payment(
        id=new_id(),
        wallet_id=source.id,
        merchant_wallet_id=merchant.id,
        amount_minor=payload["amount_minor"],
        currency="RUB",
        description=payload.get("description", ""),
        status="CREATED",
        provider_behavior=behavior,
        risk_score=risk["score"],
        risk_decision=risk["decision"],
        refunded_minor=0,
        version=1,
    )
    session.add(payment)
    await session.flush()
    timeline(
        session,
        payment.id,
        "payment.created",
        "Payment created",
        data={"amount_minor": payment.amount_minor},
    )
    transition(session, payment, "PENDING")
    timeline(
        session,
        payment.id,
        "risk.evaluated",
        "Risk rules evaluated",
        risk["explanation"],
        risk["decision"],
        risk,
    )
    if risk["decision"] == "DECLINE":
        transition(session, payment, "FAILED", "Declined by risk rules before reservation.")
        event(session, "payment.failed", {"payment_id": payment.id, "owner_id": source.owner_id})
    else:
        hold = await reserve(session, source, payment.amount_minor, f"payment:{payment.id}")
        payment.hold_id = hold.id
        timeline(
            session,
            payment.id,
            "funds.reserved",
            "Funds reserved",
            data={"hold_id": hold.id, "amount_minor": hold.amount_minor},
        )
        if risk["decision"] == "REVIEW":
            transition(session, payment, "REQUIRES_REVIEW")
            session.add(
                RiskReview(
                    payment_id=payment.id,
                    score=risk["score"],
                    rules=risk["rules"],
                    explanation=risk["explanation"],
                    rules_version=risk["rules_version"],
                    status="PENDING",
                )
            )
            event(
                session,
                "risk.review_required",
                {"payment_id": payment.id, "owner_id": source.owner_id},
            )
        else:
            transition(session, payment, "AUTHORIZED")
            event(
                session,
                "payment.authorized",
                {"payment_id": payment.id, "owner_id": source.owner_id},
            )
    event(session, "payment.created", {"payment_id": payment.id, "owner_id": source.owner_id})
    audit(session, actor.id, "payment.created", payment.id)
    await session.flush()
    return payment_view(payment)


async def finalize(session: AsyncSession, payment: Payment, outcome: dict[str, Any]) -> None:
    source = await wallet_for(session, payment.wallet_id)
    destination = await wallet_for(session, payment.merchant_wallet_id)
    await lock_accounts(session, [source.account_id, destination.account_id])
    if outcome["status"] == "SUCCEEDED":
        assert payment.hold_id is not None
        await finish_hold(session, payment.hold_id, "CAPTURED")
        journal = await post_journal(
            session,
            "PAYMENT",
            f"payment:{payment.id}",
            [
                PostingInstruction(source.account_id, Side.CREDIT, payment.amount_minor),
                PostingInstruction(destination.account_id, Side.DEBIT, payment.amount_minor),
            ],
            payment.description,
        )
        payment.provider_reference = outcome.get("provider_reference")
        transition(session, payment, "SUCCEEDED")
        timeline(
            session,
            payment.id,
            "ledger.posted",
            "Balanced journal posted",
            data={"journal_id": journal.id},
        )
        event(
            session,
            "payment.succeeded",
            {
                "payment_id": payment.id,
                "owner_id": source.owner_id,
                "merchant_owner_id": destination.owner_id,
                "journal_id": journal.id,
            },
        )
    elif outcome["status"] in {"FAILED", "UNAVAILABLE"} and outcome.get("no_effect"):
        assert payment.hold_id is not None
        await finish_hold(session, payment.hold_id, "RELEASED")
        transition(session, payment, "FAILED", "Provider confirmed absence of a financial effect.")
        event(session, "payment.failed", {"payment_id": payment.id, "owner_id": source.owner_id})
    else:
        transition(
            session,
            payment,
            "UNKNOWN",
            "Provider outcome is uncertain. Hold retained; no unsafe fallback.",
        )


async def process_payment(payment_id: str, actor: User | None = None) -> dict[str, Any]:
    provider = await choose_provider()
    async with SessionLocal.begin() as session:
        payment = await payment_for(session, payment_id, actor, lock=True)
        if actor:
            await wallet_for(session, payment.wallet_id, actor, spending=True)
        if payment.status != "AUTHORIZED":
            return payment_view(payment)
        if provider is None:
            return payment_view(payment)
        payment.provider = provider
        payment.processing_started_at = datetime.now(UTC)
        transition(session, payment, "PROCESSING")
        timeline(
            session,
            payment.id,
            "provider.request",
            f"Request sent to {provider}",
            data={"provider": provider, "operation_id": payment.id},
        )
        amount_minor, behavior = payment.amount_minor, payment.provider_behavior
    # External HTTP is deliberately outside all financial transactions.
    outcome = await submit(provider, payment_id, amount_minor, behavior)
    if outcome["status"] == "UNAVAILABLE" and outcome.get("no_effect"):
        fallback = await choose_provider(exclude=provider)
        if fallback:
            async with SessionLocal.begin() as session:
                payment = await payment_for(session, payment_id, lock=True)
                payment.provider = fallback
                timeline(
                    session,
                    payment.id,
                    "provider.fallback",
                    f"Safe fallback to {fallback}",
                    "First provider explicitly confirmed no effect.",
                )
            outcome = await submit(fallback, payment_id, amount_minor, "success")
    async with SessionLocal.begin() as session:
        payment = await payment_for(session, payment_id, lock=True)
        if payment.status == "PROCESSING":
            timeline(
                session,
                payment.id,
                "provider.response",
                "Provider request completed",
                status=outcome["status"],
                data={
                    "provider": outcome.get("provider"),
                    "no_effect": outcome.get("no_effect", False),
                },
            )
            await finalize(session, payment, outcome)
        await session.flush()
        return payment_view(payment)


async def resolve_payment(payment_id: str) -> dict[str, Any]:
    async with SessionLocal.begin() as session:
        payment = await payment_for(session, payment_id, lock=True)
        if (
            payment.status == "PROCESSING"
            and payment.processing_started_at
            and payment.processing_started_at < datetime.now(UTC) - timedelta(seconds=15)
        ):
            transition(
                session,
                payment,
                "UNKNOWN",
                "Recovering a stale processing lease after worker interruption.",
            )
        if payment.status != "UNKNOWN" or not payment.provider:
            return payment_view(payment)
        provider, amount_minor = payment.provider, payment.amount_minor
    outcome = await lookup(provider, payment_id, amount_minor)
    async with SessionLocal.begin() as session:
        payment = await payment_for(session, payment_id, lock=True)
        if payment.status == "UNKNOWN" and outcome.get("status") in {"SUCCEEDED", "FAILED"}:
            timeline(
                session,
                payment.id,
                "provider.reconciled",
                "Provider status verified",
                status=outcome["status"],
            )
            await finalize(
                session, payment, {**outcome, "no_effect": outcome["status"] == "FAILED"}
            )
        # NOT_FOUND after an interrupted request is not proof that acceptance cannot still occur.
        await session.flush()
        return payment_view(payment)


async def cancel_payment(session: AsyncSession, actor: User, payment_id: str) -> dict[str, Any]:
    payment = await payment_for(session, payment_id, actor, lock=True)
    source = await wallet_for(session, payment.wallet_id, actor, mutating=True)
    if payment.status == "CANCELED":
        return payment_view(payment)
    validate_transition(payment.status, "CANCELED")
    if payment.hold_id:
        await lock_accounts(session, [(await wallet_for(session, payment.wallet_id)).account_id])
        await finish_hold(session, payment.hold_id, "RELEASED")
    transition(session, payment, "CANCELED")
    audit(session, actor.id, "payment.canceled", payment.id)
    event(
        session,
        "payment.failed",
        {"payment_id": payment.id, "status": "CANCELED", "owner_id": source.owner_id},
    )
    await session.flush()
    return payment_view(payment)


async def refund_payment(
    session: AsyncSession, actor: User, payment_id: str, amount_minor: int
) -> dict[str, Any]:
    from app.modules.ledger.application import FINANCIAL_ACTORS, require_roles

    require_roles(actor, FINANCIAL_ACTORS)
    validate_amount(amount_minor)
    payment = await payment_for(session, payment_id, actor, lock=True)
    if payment.status not in {"SUCCEEDED", "PARTIALLY_REFUNDED"}:
        raise AppError("INVALID_PAYMENT_STATE", "Only settled payments can be refunded.", 409)
    if payment.refunded_minor + amount_minor > payment.amount_minor:
        raise AppError("REFUND_LIMIT", "Refund exceeds the remaining captured amount.", 409)
    source = await wallet_for(session, payment.wallet_id)
    merchant = await wallet_for(session, payment.merchant_wallet_id)
    balances = await lock_accounts(session, [source.account_id, merchant.account_id])
    if (
        balances[merchant.account_id].balance_minor - balances[merchant.account_id].reserved_minor
        < amount_minor
    ):
        raise AppError("INSUFFICIENT_FUNDS", "Merchant has insufficient refundable balance.", 409)
    refund_id = new_id()
    journal = await post_journal(
        session,
        "REFUND",
        f"refund:{refund_id}",
        [
            PostingInstruction(merchant.account_id, Side.CREDIT, amount_minor),
            PostingInstruction(source.account_id, Side.DEBIT, amount_minor),
        ],
        f"Refund for {payment.id}",
    )
    session.add(
        Refund(
            id=refund_id,
            payment_id=payment.id,
            amount_minor=amount_minor,
            journal_id=journal.id,
            status="SUCCEEDED",
        )
    )
    payment.refunded_minor += amount_minor
    transition(
        session,
        payment,
        "REFUNDED" if payment.refunded_minor == payment.amount_minor else "PARTIALLY_REFUNDED",
    )
    timeline(
        session,
        payment.id,
        "payment.refund",
        "Compensating refund posted",
        data={"refund_id": refund_id, "journal_id": journal.id, "amount_minor": amount_minor},
    )
    event(
        session,
        "payment.refunded",
        {
            "payment_id": payment.id,
            "owner_id": source.owner_id,
            "merchant_owner_id": merchant.owner_id,
            "refund_id": refund_id,
        },
    )
    audit(session, actor.id, "payment.refunded", payment.id, {"amount_minor": amount_minor})
    await session.flush()
    return payment_view(payment)
