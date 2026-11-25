import hashlib
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import correlation_id
from app.core.errors import AppError
from app.infrastructure.database import SessionLocal
from app.infrastructure.models import (
    AuditEvent,
    Hold,
    JournalEntry,
    LedgerAccount,
    OutboxEvent,
    Payment,
    Posting,
    RiskReview,
    TimelineEvent,
    Transfer,
    User,
    Wallet,
    WebhookDelivery,
    WebhookSubscription,
)
from app.modules.auth.router import current_user
from app.modules.ledger.application import (
    FINANCIAL_ACTORS,
    FINANCIAL_READERS,
    PRIVILEGED,
    audit,
    create_wallet,
    event,
    finish_hold,
    idempotent,
    lock_accounts,
    require_roles,
    reserve,
    serialize,
    topup,
    wallet_for,
    wallet_view,
)
from app.modules.ledger.domain import MAX_AMOUNT_MINOR
from app.modules.payments.application import (
    cancel_payment,
    create_payment,
    create_transfer,
    payment_for,
    payment_view,
    process_payment,
    refund_payment,
    resolve_payment,
    transition,
)

router = APIRouter(prefix="/api/v1")
Actor = Annotated[User, Depends(current_user)]
Key = Annotated[str | None, Header(alias="Idempotency-Key")]
Money = Annotated[int, Field(strict=True, gt=0, le=MAX_AMOUNT_MINOR)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WalletInput(Input):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    currency: Literal["RUB"] = "RUB"


class AmountInput(Input):
    amount_minor: Money


class TransferInput(AmountInput):
    source_wallet_id: UUID
    destination_wallet_id: UUID
    description: str = Field(default="", max_length=300)


class PaymentInput(AmountInput):
    wallet_id: UUID
    merchant_wallet_id: UUID
    description: str = Field(default="", max_length=300)
    provider_behavior: Literal[
        "success", "decline", "unavailable", "lost_response", "configured"
    ] = "success"
    auto_process: bool = True


class DecisionInput(Input):
    decision: Literal["ALLOW", "DECLINE"]
    reason: str = Field(min_length=3, max_length=500)


class SubscriptionInput(Input):
    receiver: Literal["mock"] = "mock"
    event_types: list[str] = Field(min_length=1, max_length=20)


def page_result(items: list[Any], page: int = 1, page_size: int = 25) -> dict[str, Any]:
    return {
        "items": items[(page - 1) * page_size : page * page_size],
        "total": len(items),
        "page": page,
        "page_size": page_size,
    }


def financial_response(result: tuple[dict[str, Any], int]) -> JSONResponse:
    payload, status = result
    if status >= 400:
        payload = {**payload, "correlation_id": correlation_id.get()}
    return JSONResponse(payload, status_code=status)


async def visible_wallet_ids(actor: User) -> list[str]:
    async with SessionLocal() as session:
        query = select(Wallet.id)
        if actor.role not in FINANCIAL_READERS:
            query = query.where(Wallet.owner_id == actor.id)
        return list((await session.scalars(query)).all())


@router.get("/wallets", tags=["Wallets"])
async def wallets(
    actor: Actor, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100)
) -> dict[str, Any]:
    async with SessionLocal() as session:
        query = select(Wallet).order_by(Wallet.created_at.desc(), Wallet.id)
        if actor.role not in FINANCIAL_READERS:
            query = query.where(Wallet.owner_id == actor.id)
        rows = (await session.scalars(query)).all()
        return page_result([await wallet_view(session, row) for row in rows], page, page_size)


@router.post("/wallets", tags=["Wallets"])
async def new_wallet(body: WalletInput, actor: Actor) -> dict[str, Any]:
    require_roles(actor, FINANCIAL_ACTORS)
    async with SessionLocal.begin() as session:
        wallet = await create_wallet(session, actor.id, body.name.strip(), body.currency)
        audit(session, actor.id, "wallet.created", wallet.id)
        return await wallet_view(session, wallet)


@router.get("/merchants", tags=["Wallets"])
async def merchants(actor: Actor) -> dict[str, Any]:
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(Wallet, User.display_name)
                .join(User, Wallet.owner_id == User.id)
                .where(User.role == "merchant", User.active.is_(True), Wallet.status == "ACTIVE")
                .order_by(Wallet.name)
            )
        ).all()
        return page_result(
            [
                {"id": wallet.id, "wallet_id": wallet.id, "name": f"{name} · {wallet.name}"}
                for wallet, name in rows
            ],
            page_size=100,
        )


@router.get("/wallets/{wallet_id}", tags=["Wallets"])
async def get_wallet(wallet_id: UUID, actor: Actor) -> dict[str, Any]:
    async with SessionLocal() as session:
        return await wallet_view(session, await wallet_for(session, str(wallet_id), actor))


@router.post("/wallets/{wallet_id}/sandbox-topup", tags=["Wallets"])
async def sandbox_topup(
    wallet_id: UUID, body: AmountInput, actor: Actor, idempotency_key: Key = None
) -> JSONResponse:
    return financial_response(
        await idempotent(
            actor,
            f"topup:{wallet_id}",
            idempotency_key,
            body.model_dump(),
            lambda session: topup(session, actor, str(wallet_id), body.amount_minor),
        )
    )


@router.post("/wallets/{wallet_id}/block", tags=["Wallets"])
async def block_wallet(wallet_id: UUID, actor: Actor) -> dict[str, Any]:
    return await change_wallet_status(str(wallet_id), actor, "BLOCKED")


@router.post("/wallets/{wallet_id}/unblock", tags=["Wallets"])
async def unblock_wallet(wallet_id: UUID, actor: Actor) -> dict[str, Any]:
    return await change_wallet_status(str(wallet_id), actor, "ACTIVE")


async def change_wallet_status(wallet_id: str, actor: User, status: str) -> dict[str, Any]:
    require_roles(actor, PRIVILEGED)
    async with SessionLocal.begin() as session:
        wallet = await session.scalar(
            select(Wallet).where(Wallet.id == wallet_id).with_for_update()
        )
        if wallet is None:
            raise AppError("WALLET_NOT_FOUND", "Wallet not found.", 404)
        wallet.status = status
        audit(session, actor.id, f"wallet.{status.lower()}", wallet.id)
        return await wallet_view(session, wallet)


@router.post("/wallets/{wallet_id}/holds", tags=["Wallets"])
async def create_hold(
    wallet_id: UUID, body: AmountInput, actor: Actor, idempotency_key: Key = None
) -> JSONResponse:
    async def action(session: AsyncSession) -> dict[str, Any]:
        wallet = await wallet_for(session, str(wallet_id), actor, spending=True)
        hold = await reserve(
            session,
            wallet,
            body.amount_minor,
            "manual:"
            + hashlib.sha256(f"{wallet_id}:{actor.id}:{idempotency_key}".encode()).hexdigest(),
        )
        audit(session, actor.id, "hold.created", hold.id)
        return serialize(hold)

    return financial_response(
        await idempotent(actor, f"hold:{wallet_id}", idempotency_key, body.model_dump(), action)
    )


@router.post("/holds/{hold_id}/release", tags=["Wallets"])
async def release_hold(hold_id: UUID, actor: Actor, idempotency_key: Key = None) -> JSONResponse:
    async def action(session: AsyncSession) -> dict[str, Any]:
        hold = await session.get(Hold, str(hold_id))
        if hold is None or not hold.reference_id.startswith("manual:"):
            raise AppError("HOLD_NOT_FOUND", "Manual hold not found.", 404)
        wallet = await session.scalar(select(Wallet).where(Wallet.account_id == hold.account_id))
        assert wallet is not None
        await wallet_for(session, wallet.id, actor, mutating=True)
        await lock_accounts(session, [hold.account_id])
        return serialize(await finish_hold(session, str(hold_id), "RELEASED"))

    return financial_response(
        await idempotent(actor, f"release:{hold_id}", idempotency_key, {}, action)
    )


@router.post("/transfers", tags=["Transfers"])
async def transfer(body: TransferInput, actor: Actor, idempotency_key: Key = None) -> JSONResponse:
    payload = body.model_dump(mode="json")
    return financial_response(
        await idempotent(
            actor,
            "transfer",
            idempotency_key,
            payload,
            lambda session: create_transfer(session, actor, **payload),
        )
    )


@router.get("/transfers", tags=["Transfers"])
async def transfers(
    actor: Actor, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100)
) -> dict[str, Any]:
    ids = await visible_wallet_ids(actor)
    async with SessionLocal() as session:
        rows = (
            await session.scalars(
                select(Transfer)
                .where(
                    or_(Transfer.source_wallet_id.in_(ids), Transfer.destination_wallet_id.in_(ids))
                )
                .order_by(Transfer.created_at.desc())
            )
        ).all()
        return page_result([serialize(row) for row in rows], page, page_size)


@router.post("/payments", tags=["Payments"])
async def payment_create(
    body: PaymentInput, actor: Actor, idempotency_key: Key = None
) -> JSONResponse:
    payload = body.model_dump(mode="json")
    response, status = await idempotent(
        actor,
        "payment",
        idempotency_key,
        payload,
        lambda session: create_payment(session, actor, payload),
    )
    if status == 200 and body.auto_process and response.get("status") == "AUTHORIZED":
        response = await process_payment(response["id"], actor)
    return financial_response((response, status))


@router.get("/payments", tags=["Payments"])
async def payments(
    actor: Actor,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    status: str | None = None,
    search: str = Query("", max_length=100),
    sort: Literal["created_at", "-created_at", "amount_minor", "-amount_minor"] = "-created_at",
) -> dict[str, Any]:
    ids = await visible_wallet_ids(actor)
    async with SessionLocal() as session:
        query = select(Payment).where(
            or_(Payment.wallet_id.in_(ids), Payment.merchant_wallet_id.in_(ids))
        )
        if status:
            query = query.where(Payment.status == status)
        if search:
            query = query.where(Payment.description.icontains(search, autoescape=True))
        column = Payment.amount_minor if "amount" in sort else Payment.created_at
        query = query.order_by(column.desc() if sort.startswith("-") else column.asc(), Payment.id)
        return page_result(
            [payment_view(row) for row in (await session.scalars(query)).all()], page, page_size
        )


@router.get("/payments/{payment_id}", tags=["Payments"])
async def get_payment(payment_id: UUID, actor: Actor) -> dict[str, Any]:
    async with SessionLocal() as session:
        return payment_view(await payment_for(session, str(payment_id), actor))


@router.post("/payments/{payment_id}/process", tags=["Payments"])
async def process(payment_id: UUID, actor: Actor, idempotency_key: Key = None) -> JSONResponse:
    # Durable state/unique journal effect is the process-operation idempotency boundary.
    async def action(session: AsyncSession) -> dict[str, Any]:
        row = await payment_for(session, str(payment_id), actor)
        await wallet_for(session, row.wallet_id, actor, spending=True)
        return payment_view(row)

    result, status = await idempotent(actor, f"process:{payment_id}", idempotency_key, {}, action)
    if status == 200:
        result = await process_payment(str(payment_id), actor)
    return financial_response((result, status))


@router.post("/payments/{payment_id}/resolve", tags=["Payments"])
async def resolve(payment_id: UUID, actor: Actor) -> dict[str, Any]:
    async with SessionLocal() as session:
        payment = await payment_for(session, str(payment_id), actor)
        await wallet_for(session, payment.wallet_id, actor, mutating=True)
    return await resolve_payment(str(payment_id))


@router.post("/payments/{payment_id}/cancel", tags=["Payments"])
async def cancel(payment_id: UUID, actor: Actor, idempotency_key: Key = None) -> JSONResponse:
    return financial_response(
        await idempotent(
            actor,
            f"cancel:{payment_id}",
            idempotency_key,
            {},
            lambda session: cancel_payment(session, actor, str(payment_id)),
        )
    )


@router.post("/payments/{payment_id}/refund", tags=["Payments"])
async def refund(
    payment_id: UUID, body: AmountInput, actor: Actor, idempotency_key: Key = None
) -> JSONResponse:
    return financial_response(
        await idempotent(
            actor,
            f"refund:{payment_id}",
            idempotency_key,
            body.model_dump(),
            lambda session: refund_payment(session, actor, str(payment_id), body.amount_minor),
        )
    )


@router.get("/payments/{payment_id}/timeline", tags=["Payments"])
async def payment_timeline(payment_id: UUID, actor: Actor) -> dict[str, Any]:
    async with SessionLocal() as session:
        await payment_for(session, str(payment_id), actor)
        rows = (
            await session.scalars(
                select(TimelineEvent)
                .where(TimelineEvent.payment_id == str(payment_id))
                .order_by(TimelineEvent.created_at, TimelineEvent.id)
            )
        ).all()
        items = [serialize(row) for row in rows]
        events = (
            await session.scalars(
                select(OutboxEvent).where(
                    OutboxEvent.payload["payment_id"].astext == str(payment_id)
                )
            )
        ).all()
        for row in events:
            items.append(
                {
                    "id": row.id,
                    "kind": "outbox.event",
                    "title": row.event_type,
                    "detail": f"Attempts: {row.attempts}; {row.last_error or 'no error'}",
                    "status": row.status,
                    "created_at": row.created_at.isoformat(),
                    "correlation_id": row.payload.get("correlation_id", ""),
                    "trace_id": "",
                    "data": {"event_id": row.id},
                }
            )
        event_ids = [row.id for row in events]
        for delivery in (
            await session.scalars(
                select(WebhookDelivery).where(WebhookDelivery.event_id.in_(event_ids))
            )
        ).all():
            items.append(
                {
                    "id": delivery.id,
                    "kind": "webhook.delivery",
                    "title": "Webhook delivery",
                    "detail": f"Attempts: {delivery.attempts}; {delivery.last_error or 'no error'}",
                    "status": delivery.status,
                    "created_at": delivery.created_at.isoformat(),
                    "correlation_id": "",
                    "trace_id": "",
                    "data": {"event_id": delivery.event_id},
                }
            )
        items.sort(key=lambda item: item["created_at"])
        return page_result(items, page_size=max(len(items), 1))


@router.get("/ledger/accounts", tags=["Ledger"])
async def accounts(
    actor: Actor, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100)
) -> dict[str, Any]:
    from app.infrastructure.models import BalanceProjection

    async with SessionLocal() as session:
        query = select(LedgerAccount, BalanceProjection).join(BalanceProjection)
        if actor.role not in FINANCIAL_READERS:
            query = query.where(LedgerAccount.owner_id == actor.id)
        rows = (await session.execute(query.order_by(LedgerAccount.created_at))).all()
        return page_result(
            [
                {
                    **serialize(account),
                    "balance_minor": projection.balance_minor,
                    "reserved_minor": projection.reserved_minor,
                }
                for account, projection in rows
            ],
            page,
            page_size,
        )


async def journal_items(actor: User, wallet_id: str | None = None) -> list[dict[str, Any]]:
    async with SessionLocal() as session:
        account_query = select(LedgerAccount.id)
        if wallet_id:
            wallet = await wallet_for(session, wallet_id, actor)
            account_query = account_query.where(LedgerAccount.id == wallet.account_id)
        elif actor.role not in FINANCIAL_READERS:
            account_query = account_query.where(LedgerAccount.owner_id == actor.id)
        journal_ids = select(Posting.journal_id).where(Posting.account_id.in_(account_query))
        entries = (
            await session.scalars(
                select(JournalEntry)
                .where(JournalEntry.id.in_(journal_ids))
                .order_by(JournalEntry.created_at.desc())
            )
        ).all()
        posting_rows = (
            await session.scalars(
                select(Posting).where(Posting.journal_id.in_([row.id for row in entries]))
            )
        ).all()
        return [
            {
                **serialize(row, {"created_txid"}),
                "postings": [
                    serialize(posting) for posting in posting_rows if posting.journal_id == row.id
                ],
            }
            for row in entries
        ]


@router.get("/ledger/journal", tags=["Ledger"])
async def journal(
    actor: Actor, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100)
) -> dict[str, Any]:
    return page_result(await journal_items(actor), page, page_size)


@router.get("/wallets/{wallet_id}/transactions", tags=["Wallets"])
async def transactions(
    wallet_id: UUID,
    actor: Actor,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
) -> dict[str, Any]:
    return page_result(await journal_items(actor, str(wallet_id)), page, page_size)


@router.get("/risk/reviews", tags=["Risk"])
async def reviews(
    actor: Actor, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100)
) -> dict[str, Any]:
    require_roles(actor, FINANCIAL_READERS)
    async with SessionLocal() as session:
        rows = (
            await session.scalars(select(RiskReview).order_by(RiskReview.created_at.desc()))
        ).all()
        return page_result([serialize(row) for row in rows], page, page_size)


@router.post("/risk/reviews/{review_id}/decision", tags=["Risk"])
async def decide_review(review_id: UUID, body: DecisionInput, actor: Actor) -> dict[str, Any]:
    require_roles(actor, {"risk_analyst", "administrator"})
    async with SessionLocal.begin() as session:
        review = await session.scalar(
            select(RiskReview).where(RiskReview.id == str(review_id)).with_for_update()
        )
        if review is None:
            raise AppError("REVIEW_NOT_FOUND", "Risk review not found.", 404)
        if review.status != "PENDING":
            raise AppError("REVIEW_COMPLETED", "Risk review was already decided.", 409)
        payment = await payment_for(session, review.payment_id, lock=True)
        if payment.status != "REQUIRES_REVIEW":
            raise AppError("INVALID_PAYMENT_STATE", "Payment is no longer awaiting review.", 409)
        review.status, review.reviewer_id, review.reason = body.decision, actor.id, body.reason
        payment.risk_decision = body.decision
        if body.decision == "ALLOW":
            transition(session, payment, "AUTHORIZED", body.reason)
            source = await wallet_for(session, payment.wallet_id)
            event(
                session,
                "payment.authorized",
                {"payment_id": payment.id, "owner_id": source.owner_id},
            )
        else:
            source = await wallet_for(session, payment.wallet_id)
            await lock_accounts(session, [source.account_id])
            assert payment.hold_id is not None
            await finish_hold(session, payment.hold_id, "RELEASED")
            transition(session, payment, "FAILED", body.reason)
        audit(session, actor.id, "risk.decision", review.id, body.model_dump())
        await session.flush()
        return serialize(review)


@router.get("/outbox", tags=["Events"])
async def outbox(
    actor: Actor, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100)
) -> dict[str, Any]:
    require_roles(actor, PRIVILEGED)
    async with SessionLocal() as session:
        rows = (
            await session.scalars(select(OutboxEvent).order_by(OutboxEvent.created_at.desc()))
        ).all()
        return page_result([serialize(row) for row in rows], page, page_size)


@router.get("/webhooks", tags=["Webhooks"])
async def webhooks(
    actor: Actor, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100)
) -> dict[str, Any]:
    async with SessionLocal() as session:
        query = select(WebhookDelivery).join(WebhookSubscription)
        if actor.role not in PRIVILEGED:
            query = query.where(WebhookSubscription.owner_id == actor.id)
        rows = (await session.scalars(query.order_by(WebhookDelivery.created_at.desc()))).all()
        return page_result([serialize(row) for row in rows], page, page_size)


@router.get("/webhook-subscriptions", tags=["Webhooks"])
async def subscriptions(actor: Actor) -> dict[str, Any]:
    async with SessionLocal() as session:
        rows = (
            await session.scalars(
                select(WebhookSubscription).where(WebhookSubscription.owner_id == actor.id)
            )
        ).all()
        return page_result([serialize(row) for row in rows])


@router.post("/webhook-subscriptions", tags=["Webhooks"])
async def subscribe(body: SubscriptionInput, actor: Actor) -> dict[str, Any]:
    allowed = {
        "payment.created",
        "payment.authorized",
        "payment.succeeded",
        "payment.failed",
        "payment.refunded",
        "wallet.balance_changed",
        "risk.review_required",
    }
    if not set(body.event_types) <= allowed:
        raise AppError("INVALID_EVENT_TYPE", "Unsupported event subscription.", 422)
    async with SessionLocal.begin() as session:
        row = await session.scalar(
            select(WebhookSubscription)
            .where(
                WebhookSubscription.owner_id == actor.id,
                WebhookSubscription.receiver == body.receiver,
            )
            .with_for_update()
        )
        if row is None:
            row = WebhookSubscription(
                owner_id=actor.id, receiver=body.receiver, event_types=body.event_types, active=True
            )
            session.add(row)
        else:
            row.event_types = body.event_types
            row.active = True
        audit(session, actor.id, "webhook.subscription", actor.id)
        await session.flush()
        return serialize(row)


@router.post("/webhooks/{delivery_id}/replay", tags=["Webhooks"])
async def replay_webhook(delivery_id: UUID, actor: Actor) -> dict[str, Any]:
    async with SessionLocal.begin() as session:
        row = await session.scalar(
            select(WebhookDelivery).where(WebhookDelivery.id == str(delivery_id)).with_for_update()
        )
        subscription = await session.get(WebhookSubscription, row.subscription_id) if row else None
        if (
            row is None
            or subscription is None
            or (actor.role not in PRIVILEGED and subscription.owner_id != actor.id)
        ):
            raise AppError("WEBHOOK_NOT_FOUND", "Webhook delivery not found.", 404)
        if row.status == "DELIVERED":
            raise AppError("ALREADY_DELIVERED", "A delivered event cannot be replayed.", 409)
        from datetime import UTC, datetime

        row.status, row.attempts, row.last_error, row.next_attempt_at = (
            "PENDING",
            0,
            None,
            datetime.now(UTC),
        )
        audit(session, actor.id, "webhook.replayed", row.id)
        return serialize(row)


@router.get("/audit", tags=["Audit"])
async def audit_log(
    actor: Actor, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100)
) -> dict[str, Any]:
    require_roles(actor, PRIVILEGED)
    async with SessionLocal() as session:
        rows = (
            await session.scalars(select(AuditEvent).order_by(AuditEvent.created_at.desc()))
        ).all()
        return page_result([serialize(row) for row in rows], page, page_size)
