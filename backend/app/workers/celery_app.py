import asyncio
import logging
from collections.abc import Awaitable
from typing import Any

from celery import Celery
from sqlalchemy import select

from app.infrastructure.database import SessionLocal, engine
from app.infrastructure.models import Payment
from app.modules.outbox.application import broker_url, transport_suffix

logger = logging.getLogger("ledgerflow.scheduler")
celery_app = Celery("ledgerflow", broker=broker_url())
celery_app.conf.update(
    task_default_queue=f"ledgerflow.tasks{transport_suffix()}",
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_ignore_result=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    broker_connection_timeout=3,
    task_time_limit=120,
    task_soft_time_limit=110,
    timezone="UTC",
    beat_schedule={
        "recover-payments": {"task": "ledgerflow.payments.maintenance", "schedule": 3.0},
        "scheduled-reconciliation": {"task": "ledgerflow.reconciliation.run", "schedule": 60.0},
    },
)


async def maintenance() -> dict[str, Any]:
    from app.modules.payments.application import process_payment, resolve_payment

    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(Payment.id, Payment.status)
                .where(Payment.status.in_(["AUTHORIZED", "PROCESSING", "UNKNOWN"]))
                .order_by(Payment.created_at)
                .limit(25)
            )
        ).all()
    outcomes: dict[str, int] = {}
    failures = 0
    for payment_id, state in rows:
        try:
            result = await (
                process_payment(payment_id)
                if state == "AUTHORIZED"
                else resolve_payment(payment_id)
            )
            outcomes[result["status"]] = outcomes.get(result["status"], 0) + 1
        except Exception as error:
            failures += 1
            logger.warning("payment.maintenance.retry type=%s", type(error).__name__)
    return {"checked": len(rows), "outcomes": outcomes, "failures": failures}


def run_task(work: Awaitable[dict[str, Any]]) -> dict[str, Any]:
    async def managed() -> dict[str, Any]:
        try:
            return await work
        finally:
            # Celery synchronous tasks create a new loop each time. Never reuse an
            # asyncpg pooled connection that belongs to a prior task's closed loop.
            await engine.dispose()

    return asyncio.run(managed())


@celery_app.task(name="ledgerflow.payments.maintenance")
def maintain_payments() -> dict[str, Any]:
    return run_task(maintenance())


@celery_app.task(name="ledgerflow.reconciliation.run")
def scheduled_reconciliation() -> dict[str, Any]:
    from app.modules.reconciliation.application import run_reconciliation

    return run_task(run_reconciliation(actor_id=None))
