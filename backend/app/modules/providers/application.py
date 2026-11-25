import asyncio
import random
import time
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.core.config import get_settings
from app.infrastructure.database import SessionLocal
from app.infrastructure.models import ProviderCircuit


class ProviderReceipt(BaseModel):
    model_config = ConfigDict(extra="ignore")
    operation_id: str
    amount_minor: int = Field(strict=True, gt=0)
    currency: Literal["RUB"]
    status: Literal["SUCCEEDED", "FAILED"]
    provider_reference: str = Field(min_length=1, max_length=100)
    fee_minor: int = Field(default=0, strict=True, ge=0)


def validate_receipt(payload: Any, operation_id: str, amount_minor: int) -> dict[str, Any]:
    receipt = ProviderReceipt.model_validate(payload)
    if receipt.operation_id != operation_id or receipt.amount_minor != amount_minor:
        raise ValueError("Provider receipt does not match the requested financial operation.")
    if receipt.fee_minor > receipt.amount_minor:
        raise ValueError("Provider fee exceeds the payment amount.")
    return receipt.model_dump()


def provider_url(name: str) -> str:
    settings = get_settings()
    return {"alpha": settings.provider_alpha_url, "beta": settings.provider_beta_url}[name]


async def choose_provider(exclude: str | None = None) -> str | None:
    async with SessionLocal.begin() as session:
        for name in ["alpha", "beta"]:
            if name == exclude:
                continue
            row = await session.scalar(
                select(ProviderCircuit).where(ProviderCircuit.name == name).with_for_update()
            )
            if row is None:
                continue
            if row.opened_until and row.opened_until > datetime.now(UTC):
                continue
            if row.failures >= 3:
                # A single bounded half-open probe owns the lease; peers route elsewhere.
                row.opened_until = datetime.now(UTC) + timedelta(seconds=20)
            return name
    return None


async def record_result(name: str, succeeded: bool, latency_ms: int) -> None:
    async with SessionLocal.begin() as session:
        row = await session.scalar(
            select(ProviderCircuit).where(ProviderCircuit.name == name).with_for_update()
        )
        assert row is not None
        row.total_requests += 1
        row.last_latency_ms = latency_ms
        if succeeded:
            row.successes += 1
            row.failures = 0
            row.opened_until = None
        else:
            row.failures += 1
            if row.failures >= 3:
                row.opened_until = datetime.now(UTC) + timedelta(seconds=20)


async def submit(name: str, operation_id: str, amount_minor: int, behavior: str) -> dict[str, Any]:
    for attempt in range(2):
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(
                timeout=get_settings().provider_timeout_seconds, trust_env=False
            ) as client:
                response = await client.post(
                    f"{provider_url(name)}/payments",
                    json={
                        "operation_id": operation_id,
                        "amount_minor": amount_minor,
                        "currency": "RUB",
                        "behavior": behavior,
                    },
                )
            latency = round((time.monotonic() - started) * 1000)
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("Invalid provider response shape.")
            if response.status_code == 200 and payload.get("status") in {"SUCCEEDED", "FAILED"}:
                receipt = validate_receipt(payload, operation_id, amount_minor)
                await record_result(name, True, latency)
                return {**receipt, "provider": name, "no_effect": receipt["status"] == "FAILED"}
            detail = payload.get("detail", {})
            no_effect = (
                response.status_code == 503
                and isinstance(detail, dict)
                and detail.get("no_effect") is True
            )
            await record_result(name, False, latency)
            if no_effect:
                if attempt == 0:
                    await asyncio.sleep(0.03 + random.uniform(0, 0.03))
                    continue
                return {"status": "UNAVAILABLE", "provider": name, "no_effect": True}
            return {"status": "UNKNOWN", "provider": name, "no_effect": False}
        except (httpx.HTTPError, ValueError):
            await record_result(name, False, round((time.monotonic() - started) * 1000))
            # A timeout may follow acceptance. Never fall back to another provider.
            return {"status": "UNKNOWN", "provider": name, "no_effect": False}
    raise AssertionError("Unreachable provider retry branch")


async def lookup(name: str, operation_id: str, amount_minor: int) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(
            timeout=get_settings().provider_timeout_seconds, trust_env=False
        ) as client:
            response = await client.get(f"{provider_url(name)}/payments/{operation_id}")
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, dict) and payload.get("status") == "NOT_FOUND":
                return {"status": "NOT_FOUND"}
            return validate_receipt(payload, operation_id, amount_minor)
    except (httpx.HTTPError, ValueError):
        return {"status": "UNKNOWN"}
