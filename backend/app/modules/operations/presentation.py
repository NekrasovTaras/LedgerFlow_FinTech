import asyncio
import csv
import io
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

import aio_pika
import httpx
from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from redis.asyncio import Redis
from sqlalchemy import func, or_, select, text

from app.api import page_result, visible_wallet_ids
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.observability import OUTBOX_BACKLOG
from app.infrastructure.database import SessionLocal
from app.infrastructure.models import (
    BalanceProjection,
    Discrepancy,
    Hold,
    OutboxEvent,
    Payment,
    ProviderCircuit,
    ReconciliationRun,
    User,
    Wallet,
    WebhookDelivery,
)
from app.modules.auth import current_user
from app.modules.ledger.application import FINANCIAL_READERS, require_roles, serialize, wallet_for
from app.modules.payments.application import payment_view
from app.modules.providers.application import provider_url
from app.modules.reconciliation.application import run_reconciliation

router = APIRouter(prefix="/api/v1", tags=["Operations"])
Actor = Annotated[User, Depends(current_user)]


def safe_integer(value: Any) -> int:
    result = int(value or 0)
    if abs(result) > 9_007_199_254_740_991:
        raise AppError(
            "AGGREGATE_TOO_LARGE", "Aggregate exceeds the exact JSON integer range.", 409
        )
    return result


@router.get("/dashboard")
async def dashboard(actor: Actor) -> dict[str, Any]:
    ids = await visible_wallet_ids(actor)
    async with SessionLocal() as session:
        balance = (
            await session.execute(
                select(
                    func.coalesce(func.sum(BalanceProjection.balance_minor), 0),
                    func.coalesce(func.sum(BalanceProjection.reserved_minor), 0),
                )
                .join(Wallet, Wallet.account_id == BalanceProjection.account_id)
                .where(Wallet.id.in_(ids))
            )
        ).one()
        payment_scope = or_(Payment.wallet_id.in_(ids), Payment.merchant_wallet_id.in_(ids))
        counts = dict(
            (
                await session.execute(
                    select(Payment.status, func.count())
                    .where(payment_scope)
                    .group_by(Payment.status)
                )
            ).all()
        )
        recent = (
            await session.scalars(
                select(Payment).where(payment_scope).order_by(Payment.created_at.desc()).limit(8)
            )
        ).all()
        volumes = (
            await session.execute(
                select(func.date(Payment.created_at), func.sum(Payment.amount_minor), func.count())
                .where(
                    payment_scope,
                    Payment.status.in_(["SUCCEEDED", "PARTIALLY_REFUNDED", "REFUNDED"]),
                )
                .group_by(func.date(Payment.created_at))
                .order_by(func.date(Payment.created_at))
            )
        ).all()
        outbox_pending = (
            await session.scalar(
                select(func.count())
                .select_from(OutboxEvent)
                .where(OutboxEvent.status.in_(["PENDING", "PUBLISHING"]))
            )
            or 0
        )
        failures = (
            await session.scalar(
                select(func.count())
                .select_from(WebhookDelivery)
                .where(WebhookDelivery.status == "DEAD")
            )
            or 0
        )
        latest_run = await session.scalar(
            select(ReconciliationRun).order_by(ReconciliationRun.created_at.desc()).limit(1)
        )
        OUTBOX_BACKLOG.set(outbox_pending)
        privileged = actor.role in FINANCIAL_READERS
        return {
            "available_minor": safe_integer(balance[0] - balance[1]),
            "reserved_minor": safe_integer(balance[1]),
            "total_payments": sum(counts.values()),
            "succeeded_payments": sum(
                counts.get(state, 0) for state in ["SUCCEEDED", "PARTIALLY_REFUNDED", "REFUNDED"]
            ),
            "failed_payments": counts.get("FAILED", 0),
            "pending_payments": sum(
                counts.get(state, 0)
                for state in [
                    "CREATED",
                    "PENDING",
                    "AUTHORIZED",
                    "PROCESSING",
                    "UNKNOWN",
                    "REQUIRES_REVIEW",
                ]
            ),
            "outbox_pending": outbox_pending if privileged else 0,
            "webhook_failures": failures if privileged else 0,
            "reconciliation_discrepancies": int(
                (latest_run.summary or {}).get("discrepancy_count", 0)
            )
            if privileged and latest_run
            else 0,
            "recent_payments": [payment_view(row) for row in recent],
            "volume_series": [
                {"date": str(day), "amount_minor": safe_integer(amount), "count": count}
                for day, amount, count in volumes
            ],
        }


@router.get("/wallets/{wallet_id}/holds")
async def holds(
    wallet_id: UUID,
    actor: Actor,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
) -> dict[str, Any]:
    async with SessionLocal() as session:
        wallet = await wallet_for(session, str(wallet_id), actor)
        rows = (
            await session.scalars(
                select(Hold)
                .where(Hold.account_id == wallet.account_id)
                .order_by(Hold.created_at.desc())
            )
        ).all()
        return page_result([serialize(row) for row in rows], page, page_size)


async def provider_snapshot(name: str) -> dict[str, Any]:
    available = False
    fee = 0
    try:
        async with httpx.AsyncClient(timeout=1, trust_env=False) as client:
            response = await client.get(f"{provider_url(name)}/health")
            response.raise_for_status()
            control = await client.get(f"{provider_url(name)}/control")
            control.raise_for_status()
            data = control.json()
            available = bool(data.get("available", True))
            fee = int(data.get("fee_bps", 0))
    except (httpx.HTTPError, ValueError):
        pass
    async with SessionLocal() as session:
        row = await session.get(ProviderCircuit, name)
        opened = bool(row and row.opened_until and row.opened_until > datetime.now(UTC))
        return {
            "name": name,
            "status": "AVAILABLE" if available else "UNAVAILABLE",
            "available": available,
            "fee_bps": fee,
            "circuit_state": "OPEN" if opened else "CLOSED",
            "success_rate": row.successes / row.total_requests if row and row.total_requests else 0,
            "latency_ms": row.last_latency_ms if row else 0,
            "total_requests": row.total_requests if row else 0,
        }


@router.get("/providers")
async def providers(actor: Actor) -> dict[str, Any]:
    return page_result(
        list(await asyncio.gather(*(provider_snapshot(name) for name in ["alpha", "beta"])))
    )


@router.get("/settings")
async def settings(actor: Actor) -> dict[str, Any]:
    config = get_settings()
    return {
        "sandbox_mode": config.sandbox_mode,
        "currency": "RUB",
        "risk_rules": {
            "version": "2026-11-v1",
            "review_threshold_minor": config.risk_review_threshold_minor,
            "decline_threshold_minor": config.risk_decline_threshold_minor,
            "velocity_per_minute": config.risk_velocity_limit,
        },
        "limits": {
            "max_operation_minor": 100_000_000_000,
            "max_request_bytes": 1_048_576,
            "access_token_minutes": config.access_token_minutes,
            "refund_policy": "Cannot exceed captured amount; compensating entries only.",
        },
    }


@router.get("/system/health")
async def system_health(actor: Actor) -> dict[str, Any]:
    config = get_settings()
    services: dict[str, str] = {}
    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
        services["postgres"] = "healthy"
    except Exception:
        services["postgres"] = "unavailable"
    try:
        async with Redis.from_url(
            config.redis_url, socket_connect_timeout=1, socket_timeout=1
        ) as cache:
            await cache.ping()
        services["redis"] = "healthy"
    except Exception:
        services["redis"] = "unavailable (financial operations remain PostgreSQL-backed)"
    try:
        connection = await aio_pika.connect(config.rabbitmq_url, timeout=1)
        await connection.close()
        services["rabbitmq"] = "healthy"
    except Exception:
        services["rabbitmq"] = "unavailable (events retained in outbox)"
    snapshots = await asyncio.gather(provider_snapshot("alpha"), provider_snapshot("beta"))
    for provider in snapshots:
        services[f"provider_{provider['name']}"] = (
            "healthy" if provider["available"] else "unavailable"
        )
    return {
        "status": "healthy"
        if all(value == "healthy" for value in services.values())
        else "degraded",
        "services": services,
        "sandbox_mode": config.sandbox_mode,
    }


@router.get("/reconciliation/runs")
async def reconciliation_runs(
    actor: Actor, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100)
) -> dict[str, Any]:
    require_roles(actor, FINANCIAL_READERS)
    async with SessionLocal() as session:
        rows = (
            await session.scalars(
                select(ReconciliationRun).order_by(ReconciliationRun.created_at.desc())
            )
        ).all()
        return page_result(
            [
                {**serialize(row), "discrepancy_count": row.summary.get("discrepancy_count", 0)}
                for row in rows
            ],
            page,
            page_size,
        )


@router.post("/reconciliation/runs")
async def new_reconciliation(actor: Actor) -> dict[str, Any]:
    require_roles(actor, {"administrator", "operator"})
    return await run_reconciliation(actor.id)


@router.get("/reconciliation/runs/{run_id}")
async def get_reconciliation(run_id: UUID, actor: Actor) -> dict[str, Any]:
    require_roles(actor, FINANCIAL_READERS)
    async with SessionLocal() as session:
        row = await session.get(ReconciliationRun, str(run_id))
        if row is None:
            raise AppError("RUN_NOT_FOUND", "Reconciliation run not found.", 404)
        discrepancies = (
            await session.scalars(select(Discrepancy).where(Discrepancy.run_id == row.id))
        ).all()
        return {
            **serialize(row),
            "discrepancy_count": len(discrepancies),
            "discrepancies": [
                {
                    **serialize(item),
                    "kind": item.classification,
                    "entity_id": item.reference_id,
                    "description": item.detail,
                }
                for item in discrepancies
            ],
        }


@router.get("/reconciliation/runs/{run_id}/export")
async def export_reconciliation(run_id: UUID, actor: Actor) -> Response:
    data = await get_reconciliation(run_id, actor)
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["classification", "reference_id", "detail", "recommendation"])
    for item in data["discrepancies"]:
        values = [
            str(item[key]) for key in ["classification", "reference_id", "detail", "recommendation"]
        ]
        writer.writerow(
            [f"'{value}" if value.startswith(("=", "+", "-", "@")) else value for value in values]
        )
    return Response(
        stream.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="reconciliation-{run_id}.csv"'},
    )
