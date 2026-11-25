"""Real PostgreSQL aborts must retry whole commands without duplicate postings."""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.database import SessionLocal, engine
from app.infrastructure.models import IdempotencyRecord, Transfer, User
from app.modules.ledger.application import create_wallet, idempotent, topup, wallet_view
from app.modules.payments.application import create_transfer

pytestmark = [pytest.mark.integration, pytest.mark.concurrency]


async def test_real_deadlock_retries_entire_command_and_posts_once(migrated_database):
    async with SessionLocal.begin() as session:
        actor = await session.scalar(select(User).where(User.email == "admin@ledgerflow.local"))
        assert actor is not None
        source = await create_wallet(session, actor.id, "Deadlock source")
        target = await create_wallet(session, actor.id, "Deadlock target")
        await topup(session, actor, source.id, 1000)

    source_locked = asyncio.Event()
    target_locked = asyncio.Event()
    competitor_waiting = asyncio.Event()
    attempts = 0
    aborts = []
    key = str(uuid4())

    def record_abort(context):
        aborts.append(getattr(context.original_exception, "sqlstate", None))

    # Deliberately opposite locking exists only in this test to force a real
    # PostgreSQL deadlock. Production paths retain deterministic lock ordering.
    async def competitor():
        await source_locked.wait()
        async with SessionLocal.begin() as session:
            await session.execute(text("SET LOCAL deadlock_timeout = '5s'"))
            await session.execute(
                text("SELECT 1 FROM balance_projections WHERE account_id = :id FOR UPDATE"),
                {"id": target.account_id},
            )
            target_locked.set()
            competitor_waiting.set()
            await session.execute(
                text("SELECT 1 FROM balance_projections WHERE account_id = :id FOR UPDATE"),
                {"id": source.account_id},
            )

    async def action(session):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            await session.execute(text("SET LOCAL deadlock_timeout = '50ms'"))
            await session.execute(
                text("SELECT 1 FROM balance_projections WHERE account_id = :id FOR UPDATE"),
                {"id": source.account_id},
            )
            source_locked.set()
            await target_locked.wait()
            await competitor_waiting.wait()
            await session.execute(
                text("SELECT 1 FROM balance_projections WHERE account_id = :id FOR UPDATE"),
                {"id": target.account_id},
            )
        return await create_transfer(session, actor, source.id, target.id, 100)

    event.listen(engine.sync_engine, "handle_error", record_abort)
    try:
        async with asyncio.timeout(15), asyncio.TaskGroup() as group:
            group.create_task(competitor())
            result = group.create_task(
                idempotent(actor, "deadlock-regression", key, {"amount_minor": 100}, action)
            )
        response, status = result.result()
    finally:
        event.remove(engine.sync_engine, "handle_error", record_abort)

    assert "40P01" in aborts
    assert attempts == 2
    assert status == 200
    replay, replay_status = await idempotent(
        actor, "deadlock-regression", key, {"amount_minor": 100}, action
    )
    assert replay_status == status and replay == response
    assert attempts == 2
    async with SessionLocal() as session:
        assert (await wallet_view(session, source))["available_minor"] == 900
        assert (await wallet_view(session, target))["available_minor"] == 100
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Transfer)
                .where(Transfer.source_wallet_id == source.id)
            )
            == 1
        )


async def test_three_serialization_aborts_leave_no_idempotency_result(migrated_database):
    async with SessionLocal() as session:
        actor = await session.scalar(select(User).where(User.email == "admin@ledgerflow.local"))
        assert actor is not None
    attempts = 0
    key = str(uuid4())

    async def abort_transaction(session):
        nonlocal attempts
        attempts += 1
        # Explicit server-side fault injection tests the bounded retry policy;
        # this is not claimed as a naturally occurring SSI conflict.
        await session.execute(
            text("DO $$ BEGIN RAISE EXCEPTION 'test abort' USING ERRCODE = '40001'; END $$")
        )
        raise AssertionError("PostgreSQL must abort the transaction")

    with pytest.raises(DBAPIError) as failure:
        await idempotent(actor, "retry-exhaustion", key, {}, abort_transaction)
    assert getattr(failure.value.orig, "sqlstate", None) == "40001"
    assert attempts == 3
    async with SessionLocal() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(IdempotencyRecord)
                .where(
                    IdempotencyRecord.actor_id == actor.id,
                    IdempotencyRecord.operation == "retry-exhaustion",
                    IdempotencyRecord.key == key,
                )
            )
            == 0
        )
