import asyncio
from uuid import uuid4

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.core.errors import AppError
from app.infrastructure.database import SessionLocal
from app.infrastructure.models import JournalEntry, Posting, User
from app.modules.ledger.application import (
    create_wallet,
    idempotent,
    topup,
    wallet_view,
)
from app.modules.ledger.domain import PostingInstruction, Side, validate_amount, validate_journal
from app.modules.payments.application import create_transfer


@given(st.integers(min_value=1, max_value=100_000_000_000))
def test_balanced_instructions_preserve_value(amount: int) -> None:
    validate_journal(
        [PostingInstruction("a", Side.DEBIT, amount), PostingInstruction("b", Side.CREDIT, amount)]
    )


@pytest.mark.parametrize("amount", [0, -1, 1.5, True, 100_000_000_001])
def test_amount_rejects_non_integer_or_out_of_range(amount) -> None:
    with pytest.raises(AppError):
        validate_amount(amount)


def test_journal_rejects_unbalanced() -> None:
    with pytest.raises(AppError):
        validate_journal(
            [PostingInstruction("a", Side.DEBIT, 100), PostingInstruction("b", Side.CREDIT, 99)]
        )


async def accounts() -> tuple:
    async with SessionLocal.begin() as session:
        actor = User(
            email=f"ledger-{uuid4()}@example.test",
            display_name="Ledger test",
            password_hash="not-a-login-account",
            role="customer",
            active=True,
        )
        other = User(
            email=f"ledger-{uuid4()}@example.test",
            display_name="Recipient",
            password_hash="not-a-login-account",
            role="customer",
            active=True,
        )
        session.add_all([actor, other])
        await session.flush()
        source = await create_wallet(session, actor.id, "Source")
        target = await create_wallet(session, other.id, "Target")
        await topup(session, actor, source.id, 100_000)
    return actor, source, target


@pytest.mark.integration
async def test_topup_and_transfer_are_balanced(migrated_database) -> None:
    actor, source, target = await accounts()
    async with SessionLocal.begin() as session:
        result = await create_transfer(session, actor, source.id, target.id, 12_345)
        assert result["amount_minor"] == 12_345
    async with SessionLocal() as session:
        assert (await wallet_view(session, source))["available_minor"] == 87_655
        assert (await wallet_view(session, target))["available_minor"] == 12_345


@pytest.mark.integration
@pytest.mark.concurrency
async def test_one_hundred_identical_requests_create_one_transfer(migrated_database) -> None:
    actor, source, target = await accounts()
    payload = {"source": source.id, "destination": target.id, "amount_minor": 10_000}

    async def send():
        return await idempotent(
            actor,
            "transfer-test",
            "same-key",
            payload,
            lambda session: create_transfer(session, actor, source.id, target.id, 10_000),
        )

    responses = await asyncio.gather(*(send() for _ in range(100)))
    assert {status for _, status in responses} == {200}
    assert len({body["id"] for body, _ in responses}) == 1
    async with SessionLocal() as session:
        assert (await wallet_view(session, source))["available_minor"] == 90_000
        assert (await wallet_view(session, target))["available_minor"] == 10_000
    with pytest.raises(AppError, match="different parameters"):
        await idempotent(
            actor,
            "transfer-test",
            "same-key",
            {**payload, "amount_minor": 1},
            lambda session: create_transfer(session, actor, source.id, target.id, 1),
        )


@pytest.mark.integration
@pytest.mark.concurrency
async def test_twenty_debits_cannot_overdraw(migrated_database) -> None:
    actor, source, target = await accounts()

    async def spend(index):
        return await idempotent(
            actor,
            "spend",
            f"debit-{index}",
            {"amount_minor": 10_000},
            lambda session: create_transfer(session, actor, source.id, target.id, 10_000),
        )

    responses = await asyncio.gather(*(spend(index) for index in range(20)))
    assert sum(status == 200 for _, status in responses) == 10
    assert (
        sum(
            status == 409 and body["error"]["code"] == "INSUFFICIENT_FUNDS"
            for body, status in responses
        )
        == 10
    )
    async with SessionLocal() as session:
        assert (await wallet_view(session, source))["available_minor"] == 0
        assert (await wallet_view(session, target))["available_minor"] == 100_000


@pytest.mark.integration
@pytest.mark.parametrize(
    "operation",
    [
        "UPDATE postings SET amount_minor = amount_minor + 1 WHERE id = :id",
        "DELETE FROM postings WHERE id = :id",
        "UPDATE journal_entries SET description = 'tampered' WHERE id = :journal",
        "DELETE FROM journal_entries WHERE id = :journal",
        "UPDATE balance_projections SET balance_minor = 999999 WHERE account_id = :account",
    ],
)
async def test_database_rejects_history_mutation(migrated_database, operation) -> None:
    actor, source, target = await accounts()
    async with SessionLocal() as session:
        posting = await session.scalar(
            select(Posting).where(Posting.account_id == source.account_id)
        )
        assert posting is not None
        params = {"id": posting.id, "journal": posting.journal_id, "account": source.account_id}
    with pytest.raises(DBAPIError):
        async with SessionLocal.begin() as session:
            await session.execute(text(operation), params)


@pytest.mark.integration
async def test_unbalanced_commit_rolls_back_projection(migrated_database) -> None:
    actor, source, target = await accounts()
    reference = f"invalid:{uuid4()}"
    with pytest.raises(DBAPIError):
        async with SessionLocal.begin() as session:
            entry = JournalEntry(kind="TEST", reference_id=reference, currency="RUB")
            session.add(entry)
            await session.flush()
            session.add(
                Posting(
                    journal_id=entry.id,
                    account_id=source.account_id,
                    side="DEBIT",
                    amount_minor=100,
                )
            )
    async with SessionLocal() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(JournalEntry)
                .where(JournalEntry.reference_id == reference)
            )
            == 0
        )
        assert (await wallet_view(session, source))["available_minor"] == 100_000


@pytest.mark.integration
async def test_cannot_append_balanced_postings_to_committed_journal(migrated_database) -> None:
    actor, source, target = await accounts()
    async with SessionLocal() as session:
        posting = await session.scalar(
            select(Posting).where(Posting.account_id == source.account_id)
        )
        journal_id = posting.journal_id
    with pytest.raises(DBAPIError):
        async with SessionLocal.begin() as session:
            session.add_all(
                [
                    Posting(
                        journal_id=journal_id,
                        account_id=source.account_id,
                        side="CREDIT",
                        amount_minor=10,
                    ),
                    Posting(
                        journal_id=journal_id,
                        account_id=target.account_id,
                        side="DEBIT",
                        amount_minor=10,
                    ),
                ]
            )
