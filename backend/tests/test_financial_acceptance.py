"""Financial acceptance checks against real PostgreSQL, with synthetic accounts."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.security import access_token, token_digest
from app.infrastructure.database import SessionLocal, engine
from app.infrastructure.models import (
    AuthSession,
    BalanceProjection,
    Hold,
    IdempotencyRecord,
    JournalEntry,
    LedgerAccount,
    OutboxEvent,
    Payment,
    Posting,
    TimelineEvent,
    User,
    Wallet,
    new_id,
)
from app.modules.ledger.application import (
    create_wallet,
    event,
    finish_hold,
    idempotent,
    post_journal,
    reserve,
    topup,
    wallet_view,
)
from app.modules.ledger.domain import PostingInstruction, Side
from app.modules.payments.application import create_payment, create_transfer

pytestmark = pytest.mark.integration


@dataclass(frozen=True)
class FinancialEnvironment:
    actor: User
    source: Wallet
    recipient: User
    target: Wallet
    merchant: User
    merchant_wallet: Wallet


async def make_environment(funding: int = 100_000) -> FinancialEnvironment:
    async with SessionLocal.begin() as session:
        users = [
            User(
                email=f"financial-{uuid4().hex}@example.test",
                display_name=label,
                password_hash="not-a-password-login-account",
                role=role,
                active=True,
            )
            for label, role in [
                ("Synthetic payer", "customer"),
                ("Synthetic recipient", "customer"),
                ("Synthetic merchant", "merchant"),
            ]
        ]
        session.add_all(users)
        await session.flush()
        actor, recipient, merchant = users
        source = await create_wallet(session, actor.id, "Acceptance source")
        target = await create_wallet(session, recipient.id, "Acceptance recipient")
        merchant_wallet = await create_wallet(session, merchant.id, "Acceptance merchant")
        await topup(session, actor, source.id, funding)
    return FinancialEnvironment(actor, source, recipient, target, merchant, merchant_wallet)


@pytest_asyncio.fixture
async def financial_env(migrated_database: None) -> FinancialEnvironment:
    return await make_environment()


async def bearer_for(user: User) -> dict[str, str]:
    """Issue an explicit test session; login behavior is covered by test_auth.py."""
    async with SessionLocal.begin() as session:
        row = AuthSession(
            id=new_id(),
            user_id=user.id,
            token_hash=token_digest(uuid4().hex),
            family_id=new_id(),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            user_agent="LedgerFlow financial acceptance tests",
        )
        session.add(row)
        await session.flush()
        token = access_token(user.id, row.id)
    return {"Authorization": f"Bearer {token}"}


def payment_payload(env: FinancialEnvironment, amount: int = 10_000, **extra: Any) -> dict:
    return {
        "wallet_id": env.source.id,
        "merchant_wallet_id": env.merchant_wallet.id,
        "amount_minor": amount,
        "description": "Synthetic acceptance payment",
        "auto_process": False,
        **extra,
    }


async def balances(env: FinancialEnvironment) -> tuple[dict, dict, dict]:
    async with SessionLocal() as session:
        return (
            await wallet_view(session, env.source),
            await wallet_view(session, env.target),
            await wallet_view(session, env.merchant_wallet),
        )


@pytest.mark.concurrency
async def test_one_hundred_identical_payment_requests_share_one_hold_and_result(
    client: httpx.AsyncClient, financial_env: FinancialEnvironment
) -> None:
    env = financial_env
    headers = {**await bearer_for(env.actor), "Idempotency-Key": uuid4().hex}
    payload = payment_payload(env)
    responses = await asyncio.gather(
        *(client.post("/api/v1/payments", json=payload, headers=headers) for _ in range(100))
    )
    assert {response.status_code for response in responses} == {200}
    bodies = [response.json() for response in responses]
    assert all(body == bodies[0] for body in bodies)
    payment_id = bodies[0]["id"]
    async with SessionLocal() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(Payment).where(Payment.wallet_id == env.source.id)
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Hold)
                .where(Hold.account_id == env.source.account_id)
            )
            == 1
        )
        row = await session.get(Payment, payment_id)
        assert row is not None and row.status == "AUTHORIZED"
    source, _, merchant = await balances(env)
    assert (source["posted_minor"], source["available_minor"], source["reserved_minor"]) == (
        100_000,
        90_000,
        10_000,
    )
    assert merchant["posted_minor"] == 0
    conflicting = await client.post(
        "/api/v1/payments", json={**payload, "amount_minor": 10_001}, headers=headers
    )
    assert conflicting.status_code == 409
    assert conflicting.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


@pytest.mark.concurrency
async def test_twenty_distinct_payment_reservations_allow_exactly_ten(
    financial_env: FinancialEnvironment,
) -> None:
    env = financial_env
    payload = payment_payload(env)

    async def attempt(index: int) -> tuple[dict, int]:
        return await idempotent(
            env.actor,
            "acceptance-payment",
            f"request-{index}",
            payload,
            lambda session: create_payment(session, env.actor, payload, bypass_risk=True),
        )

    outcomes = await asyncio.gather(*(attempt(index) for index in range(20)))
    assert sum(status == 200 for _, status in outcomes) == 10
    assert (
        sum(
            status == 409 and body["error"]["code"] == "INSUFFICIENT_FUNDS"
            for body, status in outcomes
        )
        == 10
    )
    assert {body["status"] for body, status in outcomes if status == 200} == {"AUTHORIZED"}
    source, _, merchant = await balances(env)
    assert source["posted_minor"] == source["reserved_minor"] == 100_000
    assert source["available_minor"] == merchant["posted_minor"] == 0
    async with SessionLocal() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(Payment).where(Payment.wallet_id == env.source.id)
            )
            == 10
        )


@pytest.mark.concurrency
async def test_opposite_transfers_preserve_both_balances_without_deadlock(
    financial_env: FinancialEnvironment,
) -> None:
    env = financial_env
    async with SessionLocal.begin() as session:
        await topup(session, env.recipient, env.target.id, 100_000)

    async def transfer(index: int) -> tuple[dict, int]:
        actor, source, target = (
            (env.actor, env.source, env.target)
            if index % 2 == 0
            else (env.recipient, env.target, env.source)
        )
        return await idempotent(
            actor,
            "opposite-transfer",
            str(index),
            {"amount_minor": 100},
            lambda session: create_transfer(session, actor, source.id, target.id, 100),
        )

    outcomes = await asyncio.wait_for(
        asyncio.gather(*(transfer(index) for index in range(40))), timeout=30
    )
    assert {status for _, status in outcomes} == {200}
    source, target, _ = await balances(env)
    assert source["available_minor"] == target["available_minor"] == 100_000


async def test_failed_operation_rolls_back_journal_hold_and_outbox_together(
    financial_env: FinancialEnvironment,
) -> None:
    env = financial_env
    marker = f"rollback:{uuid4()}"

    async def deliberately_failing_operation(session) -> dict:
        await reserve(session, env.source, 100, marker)
        await finish_hold(
            session,
            (await session.scalar(select(Hold.id).where(Hold.reference_id == marker))),
            "RELEASED",
        )
        await post_journal(
            session,
            "TEST_ROLLBACK",
            marker,
            [
                PostingInstruction(env.source.account_id, Side.CREDIT, 100),
                PostingInstruction(env.target.account_id, Side.DEBIT, 100),
            ],
        )
        event(session, "wallet.balance_changed", {"marker": marker})
        raise AppError("SYNTHETIC_FAILURE", "Abort the complete operation.", 409)

    body, status = await idempotent(
        env.actor, "rollback-test", marker, {}, deliberately_failing_operation
    )
    assert status == 409 and body["error"]["code"] == "SYNTHETIC_FAILURE"
    async with SessionLocal() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(Hold).where(Hold.reference_id == marker)
            )
            == 0
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(JournalEntry)
                .where(JournalEntry.reference_id == marker)
            )
            == 0
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(OutboxEvent)
                .where(OutboxEvent.payload["marker"].astext == marker)
            )
            == 0
        )
        record = await session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.actor_id == env.actor.id, IdempotencyRecord.key == marker
            )
        )
        assert record is not None and record.status_code == 409
    source, target, _ = await balances(env)
    assert (source["available_minor"], source["reserved_minor"], target["posted_minor"]) == (
        100_000,
        0,
        0,
    )


async def test_database_overwrites_spoofed_journal_transaction_id(
    financial_env: FinancialEnvironment,
) -> None:
    env = financial_env
    async with SessionLocal.begin() as session:
        entry = JournalEntry(
            kind="TEST_TXID", reference_id=uuid4().hex, currency="RUB", created_txid=0
        )
        session.add(entry)
        await session.flush()
        await session.refresh(entry)
        actual = await session.scalar(text("SELECT txid_current()"))
        assert entry.created_txid == actual and entry.created_txid != 0
        session.add_all(
            [
                Posting(
                    journal_id=entry.id,
                    account_id=env.source.account_id,
                    side="CREDIT",
                    amount_minor=1,
                ),
                Posting(
                    journal_id=entry.id,
                    account_id=env.target.account_id,
                    side="DEBIT",
                    amount_minor=1,
                ),
            ]
        )


async def test_database_rejects_nonzero_initial_projection(
    financial_env: FinancialEnvironment,
) -> None:
    account_id = new_id()
    with pytest.raises(DBAPIError):
        async with SessionLocal.begin() as session:
            session.add(
                LedgerAccount(
                    id=account_id,
                    owner_id=financial_env.actor.id,
                    name="Invalid initial balance",
                    kind="USER_WALLET",
                    currency="RUB",
                    allow_overdraft=False,
                )
            )
            await session.flush()
            session.add(
                BalanceProjection(
                    account_id=account_id,
                    balance_minor=999,
                    reserved_minor=0,
                    allow_overdraft=False,
                )
            )
    async with SessionLocal() as session:
        assert await session.get(LedgerAccount, account_id) is None


async def test_manual_hold_release_does_not_change_posted_money(
    financial_env: FinancialEnvironment,
) -> None:
    env = financial_env
    async with SessionLocal.begin() as session:
        hold = await reserve(session, env.source, 30_000, uuid4().hex)
    source, _, _ = await balances(env)
    assert (source["posted_minor"], source["available_minor"], source["reserved_minor"]) == (
        100_000,
        70_000,
        30_000,
    )
    async with SessionLocal.begin() as session:
        await finish_hold(session, hold.id, "RELEASED")
    source, _, _ = await balances(env)
    assert (source["posted_minor"], source["available_minor"], source["reserved_minor"]) == (
        100_000,
        100_000,
        0,
    )
    with pytest.raises(AppError, match="not active"):
        async with SessionLocal.begin() as session:
            await finish_hold(session, hold.id, "RELEASED")


async def test_missing_account_and_invalid_journal_have_no_partial_effect(
    financial_env: FinancialEnvironment,
) -> None:
    env = financial_env
    marker = uuid4().hex
    with pytest.raises(AppError, match="projection is missing"):
        async with SessionLocal.begin() as session:
            await post_journal(
                session,
                "INVALID_ACCOUNT",
                marker,
                [
                    PostingInstruction(env.source.account_id, Side.CREDIT, 10),
                    PostingInstruction(new_id(), Side.DEBIT, 10),
                ],
            )
    async with SessionLocal() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(JournalEntry)
                .where(JournalEntry.reference_id == marker)
            )
            == 0
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(TimelineEvent)
                .where(
                    TimelineEvent.payment_id.in_(
                        select(Payment.id).where(Payment.wallet_id == env.source.id)
                    )
                )
            )
            == 0
        )


@settings(max_examples=10, deadline=None, derandomize=True)
@given(st.lists(st.integers(min_value=-500, max_value=500).filter(bool), max_size=12))
def test_postgresql_transfer_sequences_preserve_conservation(
    migrated_database: None,
    moves: list[int],
) -> None:
    """Exercise real transactions using a separate engine and loop per example."""
    isolated_engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    SessionLocal.configure(bind=isolated_engine)

    async def execute_sequence() -> None:
        try:
            env = await make_environment()
            expected_source, expected_target = 100_000, 0
            for index, move in enumerate(moves):
                amount = abs(move)
                actor, source, target = (
                    (env.actor, env.source, env.target)
                    if move > 0
                    else (env.recipient, env.target, env.source)
                )
                available = expected_source if move > 0 else expected_target
                body, status = await idempotent(
                    actor,
                    "hypothesis-transfer",
                    str(index),
                    {"amount_minor": amount},
                    lambda session, actor=actor, source=source, target=target, amount=amount: (
                        create_transfer(session, actor, source.id, target.id, amount)
                    ),
                )
                if available < amount:
                    assert status == 409 and body["error"]["code"] == "INSUFFICIENT_FUNDS"
                else:
                    assert status == 200
                    expected_source -= move
                    expected_target += move
                actual_source, actual_target, _ = await balances(env)
                assert actual_source["available_minor"] == expected_source
                assert actual_target["available_minor"] == expected_target
                assert actual_source["reserved_minor"] == actual_target["reserved_minor"] == 0
                assert expected_source + expected_target == 100_000
        finally:
            await isolated_engine.dispose()

    try:
        asyncio.run(execute_sequence())
    finally:
        SessionLocal.configure(bind=engine)


async def test_manual_hold_key_is_scoped_by_wallet_and_accepts_maximum_length(
    client: httpx.AsyncClient,
    financial_env: FinancialEnvironment,
) -> None:
    env = financial_env
    headers = await bearer_for(env.actor)
    created = await client.post(
        "/api/v1/wallets", json={"name": "Second own wallet"}, headers=headers
    )
    assert created.status_code == 200
    second_id = created.json()["id"]
    funded = await client.post(
        f"/api/v1/wallets/{second_id}/sandbox-topup",
        json={"amount_minor": 10_000},
        headers={**headers, "Idempotency-Key": uuid4().hex},
    )
    assert funded.status_code == 200
    shared_headers = {**headers, "Idempotency-Key": "k" * 200}
    first = await client.post(
        f"/api/v1/wallets/{env.source.id}/holds",
        json={"amount_minor": 10},
        headers=shared_headers,
    )
    second = await client.post(
        f"/api/v1/wallets/{second_id}/holds",
        json={"amount_minor": 20},
        headers=shared_headers,
    )
    replay = await client.post(
        f"/api/v1/wallets/{env.source.id}/holds",
        json={"amount_minor": 10},
        headers=shared_headers,
    )
    assert first.status_code == second.status_code == replay.status_code == 200
    assert first.json() == replay.json()
    assert first.json()["id"] != second.json()["id"]
    first_wallet = await client.get(f"/api/v1/wallets/{env.source.id}", headers=headers)
    second_wallet = await client.get(f"/api/v1/wallets/{second_id}", headers=headers)
    assert first_wallet.json()["reserved_minor"] == 10
    assert second_wallet.json()["reserved_minor"] == 20
