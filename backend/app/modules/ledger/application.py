import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import correlation_id
from app.core.errors import AppError
from app.infrastructure.database import SessionLocal
from app.infrastructure.models import (
    AuditEvent,
    BalanceProjection,
    Hold,
    IdempotencyRecord,
    JournalEntry,
    LedgerAccount,
    OutboxEvent,
    Posting,
    TimelineEvent,
    User,
    Wallet,
    new_id,
)
from app.modules.ledger.domain import PostingInstruction, Side, validate_amount, validate_journal

PRIVILEGED = {"administrator", "operator"}
FINANCIAL_READERS = PRIVILEGED | {"risk_analyst"}
FINANCIAL_ACTORS = PRIVILEGED | {"customer", "merchant"}


def serialize(row: Any, exclude: set[str] | None = None) -> dict[str, Any]:
    omitted = exclude or set()
    return jsonable_encoder(
        {
            column.name: getattr(row, column.name)
            for column in row.__table__.columns
            if column.name not in omitted
        }
    )


def require_roles(actor: User, roles: set[str]) -> None:
    if actor.role not in roles:
        raise AppError("FORBIDDEN", "This action requires an authorized role.", 403)


async def wallet_for(
    session: AsyncSession,
    wallet_id: str,
    actor: User | None = None,
    *,
    spending: bool = False,
    mutating: bool = False,
) -> Wallet:
    if actor and (spending or mutating):
        require_roles(actor, FINANCIAL_ACTORS)
    statement = select(Wallet).where(Wallet.id == wallet_id)
    row = await session.scalar(statement.with_for_update(read=True) if spending else statement)
    if row is None or (
        actor
        and row.owner_id != actor.id
        and actor.role not in (PRIVILEGED if spending or mutating else FINANCIAL_READERS)
    ):
        raise AppError("WALLET_NOT_FOUND", "Wallet not found.", 404)
    if spending and row.status != "ACTIVE":
        raise AppError("WALLET_BLOCKED", "Wallet is blocked.", 409)
    return row


async def lock_accounts(
    session: AsyncSession, account_ids: Sequence[str]
) -> dict[str, BalanceProjection]:
    # All financial paths use this exact ordering, including opposite transfers.
    rows = (
        await session.scalars(
            select(BalanceProjection)
            .where(BalanceProjection.account_id.in_(sorted(set(account_ids))))
            .order_by(BalanceProjection.account_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).all()
    if len(rows) != len(set(account_ids)):
        raise AppError("ACCOUNT_NOT_FOUND", "Account projection is missing.", 404)
    return {row.account_id: row for row in rows}


async def create_wallet(
    session: AsyncSession, owner_id: str, name: str, currency: str = "RUB"
) -> Wallet:
    if currency != "RUB":
        raise AppError("UNSUPPORTED_CURRENCY", "Only RUB is supported.", 422)
    account = LedgerAccount(
        id=new_id(),
        name=name,
        owner_id=owner_id,
        kind="USER_WALLET",
        currency=currency,
        allow_overdraft=False,
    )
    session.add(account)
    await session.flush()
    session.add(
        BalanceProjection(
            account_id=account.id, balance_minor=0, reserved_minor=0, allow_overdraft=False
        )
    )
    wallet = Wallet(
        id=new_id(),
        owner_id=owner_id,
        account_id=account.id,
        name=name,
        currency=currency,
        status="ACTIVE",
    )
    session.add(wallet)
    await session.flush()
    return wallet


async def wallet_view(session: AsyncSession, wallet: Wallet) -> dict[str, Any]:
    projection = await session.get(BalanceProjection, wallet.account_id, populate_existing=True)
    assert projection is not None
    return {
        **serialize(wallet),
        "posted_minor": projection.balance_minor,
        "reserved_minor": projection.reserved_minor,
        "available_minor": projection.balance_minor - projection.reserved_minor,
    }


async def post_journal(
    session: AsyncSession,
    kind: str,
    reference_id: str,
    postings: Sequence[PostingInstruction],
    description: str = "",
) -> JournalEntry:
    validate_journal(postings)
    await lock_accounts(session, [item.account_id for item in postings])
    journal = JournalEntry(
        id=new_id(), kind=kind, reference_id=reference_id, currency="RUB", description=description
    )
    session.add(journal)
    await session.flush()
    # SQL triggers maintain projections; application code never writes balances.
    for item in postings:
        session.add(
            Posting(
                journal_id=journal.id,
                account_id=item.account_id,
                side=item.side.value,
                amount_minor=item.amount_minor,
            )
        )
    await session.flush()
    return journal


def event(session: AsyncSession, event_type: str, payload: dict[str, Any]) -> OutboxEvent:
    row = OutboxEvent(
        id=new_id(),
        event_type=event_type,
        payload=jsonable_encoder({**payload, "correlation_id": correlation_id.get()}),
        status="PENDING",
        attempts=0,
    )
    session.add(row)
    return row


def audit(
    session: AsyncSession,
    actor_id: str | None,
    action: str,
    resource_id: str,
    data: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditEvent(
            actor_id=actor_id,
            action=action,
            resource_id=resource_id,
            correlation_id=correlation_id.get(),
            data=data or {},
        )
    )


def timeline(
    session: AsyncSession,
    payment_id: str,
    kind: str,
    title: str,
    detail: str = "",
    status: str = "INFO",
    data: dict[str, Any] | None = None,
) -> None:
    from opentelemetry import trace

    trace_id = trace.get_current_span().get_span_context().trace_id
    session.add(
        TimelineEvent(
            payment_id=payment_id,
            kind=kind,
            title=title,
            detail=detail,
            status=status,
            correlation_id=correlation_id.get(),
            trace_id=f"{trace_id:032x}" if trace_id else "",
            data=data or {},
        )
    )


async def reserve(
    session: AsyncSession, wallet: Wallet, amount_minor: int, reference_id: str
) -> Hold:
    validate_amount(amount_minor)
    locked = await lock_accounts(session, [wallet.account_id])
    balance = locked[wallet.account_id]
    if balance.balance_minor - balance.reserved_minor < amount_minor:
        raise AppError("INSUFFICIENT_FUNDS", "Insufficient available balance.", 409)
    hold = Hold(
        id=new_id(),
        account_id=wallet.account_id,
        amount_minor=amount_minor,
        status="ACTIVE",
        reference_id=reference_id,
    )
    session.add(hold)
    await session.flush()
    return hold


async def finish_hold(session: AsyncSession, hold_id: str, status: str) -> Hold:
    row = await session.scalar(select(Hold).where(Hold.id == hold_id).with_for_update())
    if row is None or row.status != "ACTIVE":
        raise AppError("INVALID_HOLD", "Hold is not active.", 409)
    row.status = status
    await session.flush()
    return row


async def topup(
    session: AsyncSession, actor: User, wallet_id: str, amount_minor: int
) -> dict[str, Any]:
    from app.core.config import get_settings

    if not get_settings().sandbox_mode:
        raise AppError("SANDBOX_DISABLED", "Virtual funding requires sandbox mode.", 403)
    validate_amount(amount_minor)
    wallet = await wallet_for(session, wallet_id, actor, spending=True)
    funding = await session.scalar(
        select(LedgerAccount).where(LedgerAccount.system_key == "sandbox_funding")
    )
    assert funding is not None
    journal = await post_journal(
        session,
        "SANDBOX_TOPUP",
        new_id(),
        [
            PostingInstruction(wallet.account_id, Side.DEBIT, amount_minor),
            PostingInstruction(funding.id, Side.CREDIT, amount_minor),
        ],
        "Virtual sandbox funding",
    )
    event(
        session,
        "wallet.balance_changed",
        {"wallet_id": wallet.id, "owner_id": wallet.owner_id, "journal_id": journal.id},
    )
    audit(session, actor.id, "wallet.sandbox_topup", wallet.id, {"amount_minor": amount_minor})
    return {**(await wallet_view(session, wallet)), "journal_id": journal.id}


async def idempotent(
    actor: User,
    operation: str,
    key: str | None,
    payload: dict[str, Any],
    action: Callable[[AsyncSession], Awaitable[dict[str, Any]]],
) -> tuple[dict[str, Any], int]:
    if not key or len(key) > 200 or not key.isascii() or any(ord(char) < 33 for char in key):
        raise AppError(
            "IDEMPOTENCY_KEY_REQUIRED",
            "A printable Idempotency-Key of 1–200 characters is required.",
            422,
        )
    fingerprint = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()
    for attempt in range(3):
        try:
            async with SessionLocal.begin() as session:
                await session.execute(
                    insert(IdempotencyRecord)
                    .values(
                        id=new_id(),
                        actor_id=actor.id,
                        operation=operation,
                        key=key,
                        fingerprint=fingerprint,
                    )
                    .on_conflict_do_nothing(constraint="uq_idempotency_scope")
                )
                record = await session.scalar(
                    select(IdempotencyRecord)
                    .where(
                        IdempotencyRecord.actor_id == actor.id,
                        IdempotencyRecord.operation == operation,
                        IdempotencyRecord.key == key,
                    )
                    .with_for_update()
                )
                assert record is not None
                if record.fingerprint != fingerprint:
                    raise AppError(
                        "IDEMPOTENCY_CONFLICT",
                        "The key was already used with different parameters.",
                        409,
                    )
                if record.response is not None:
                    return record.response, record.status_code or 200
                try:
                    async with session.begin_nested():
                        response = jsonable_encoder(await action(session))
                        status = 200
                except AppError as error:
                    response, status = error.payload(), error.status
                record.response, record.status_code = response, status
                await session.flush()
            return response, status
        except DBAPIError as error:
            code = getattr(error.orig, "sqlstate", None) or getattr(
                getattr(error.orig, "__cause__", None), "sqlstate", None
            )
            if code not in {"40P01", "40001"} or attempt == 2:
                raise
            await asyncio.sleep(0.01 * (2**attempt))
    raise AssertionError("Unreachable transaction retry branch")
