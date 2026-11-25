"""Local, durable sandbox providers and signed webhook receiver.

SQLite stores only synthetic provider receipts. PostgreSQL remains the source of
truth for LedgerFlow's financial ledger. Each Compose provider owns its volume.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import random
import sqlite3
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

SERVICE_NAME = os.environ.get("SERVICE_NAME", "alpha")
SERVICE_KIND = os.environ.get("SERVICE_KIND", "provider")
SANDBOX_MODE = os.environ.get("SANDBOX_MODE", "false").lower() == "true"
STATE_PATH = Path(os.environ.get("STATE_PATH", "/data/receipts.sqlite3"))
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "sandbox-webhook-signing-secret")
LOST_RESPONSE_DELAY_SECONDS = float(os.environ.get("LOST_RESPONSE_DELAY_SECONDS", "5"))
MAX_BODY_BYTES = 65_536


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(STATE_PATH, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def initialize() -> None:
    if not SANDBOX_MODE:
        raise RuntimeError("Mock services require explicit SANDBOX_MODE=true")
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS receipts (
              operation_id TEXT PRIMARY KEY,
              amount_minor INTEGER NOT NULL,
              currency TEXT NOT NULL,
              status TEXT NOT NULL,
              provider_reference TEXT NOT NULL,
              fee_minor INTEGER NOT NULL,
              created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS received_events (
              event_id TEXT PRIMARY KEY,
              body_hash TEXT NOT NULL,
              payload TEXT NOT NULL,
              received_at INTEGER NOT NULL,
              deliveries INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS controls (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            );
            """
        )


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    initialize()
    yield


app = FastAPI(title=f"LedgerFlow sandbox {SERVICE_NAME}", lifespan=lifespan)


class PaymentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    amount_minor: int = Field(strict=True, gt=0, le=9_000_000_000_000_000)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    behavior: Literal["success", "decline", "unavailable", "lost_response", "configured"] = (
        "success"
    )


class ProviderControl(BaseModel):
    model_config = ConfigDict(extra="forbid")
    available: bool = True
    success_probability: float = Field(default=1, ge=0, le=1)
    delay_ms: int = Field(default=0, ge=0, le=5_000)
    fee_bps: int = Field(default=25, ge=0, le=1_000)
    force_behavior: Literal["success", "decline", "unavailable", "lost_response"] | None = None
    report_discrepancy_minor: int = Field(default=0, ge=-1_000_000, le=1_000_000)
    report_operation_id: str | None = Field(
        default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$"
    )


class ReceiverControl(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fail: bool = False


def get_control(key: str, default: dict[str, Any]) -> dict[str, Any]:
    with connect() as connection:
        row = connection.execute("SELECT value FROM controls WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def save_control(key: str, value: dict[str, Any]) -> None:
    with connect() as connection:
        connection.execute(
            "INSERT INTO controls(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )


def require_kind(kind: str) -> None:
    if SERVICE_KIND != kind:
        raise HTTPException(404, "Route not enabled for this sandbox service")


@app.get("/health")
def health() -> dict[str, Any]:
    with connect() as connection:
        connection.execute("SELECT 1")
    return {"status": "ok", "service": SERVICE_NAME, "sandbox": SANDBOX_MODE}


@app.get("/payments/{operation_id}")
def payment_status(operation_id: str) -> dict[str, Any]:
    require_kind("provider")
    with connect() as connection:
        row = connection.execute(
            "SELECT * FROM receipts WHERE operation_id = ?", (operation_id,)
        ).fetchone()
    if not row:
        return {"operation_id": operation_id, "status": "NOT_FOUND", "provider_reference": None}
    return dict(row)


@app.post("/payments")
async def create_payment(payment: PaymentRequest) -> dict[str, Any]:
    require_kind("provider")
    control = ProviderControl.model_validate(
        get_control("provider", ProviderControl().model_dump())
    )
    # BEGIN IMMEDIATE serializes idempotent writes inside this independent mock
    # provider, including two simultaneous requests for the same operation.
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM receipts WHERE operation_id = ?", (payment.operation_id,)
        ).fetchone()
        if row:
            if row["amount_minor"] != payment.amount_minor or row["currency"] != payment.currency:
                raise HTTPException(409, "Provider idempotency payload conflict")
            return dict(row)
        behavior = control.force_behavior or payment.behavior
        if not control.available or behavior == "unavailable":
            raise HTTPException(
                503,
                {
                    "code": "UNAVAILABLE",
                    "no_effect": True,
                    "message": "Sandbox provider unavailable before acceptance",
                },
            )
        if behavior == "configured":
            behavior = "success" if random.random() < control.success_probability else "decline"
        status = "FAILED" if behavior == "decline" else "SUCCEEDED"
        reference = (
            f"{SERVICE_NAME}-{hashlib.sha256(payment.operation_id.encode()).hexdigest()[:24]}"
        )
        fee_minor = payment.amount_minor * control.fee_bps // 10_000 if status == "SUCCEEDED" else 0
        connection.execute(
            "INSERT INTO receipts VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                payment.operation_id,
                payment.amount_minor,
                payment.currency,
                status,
                reference,
                fee_minor,
                int(time.time()),
            ),
        )
    # Receipt is durably committed BEFORE any delayed/lost HTTP response.
    if behavior == "lost_response":
        await asyncio.sleep(LOST_RESPONSE_DELAY_SECONDS)
        raise HTTPException(504, "Sandbox response lost after durable provider acceptance")
    if control.delay_ms:
        await asyncio.sleep(control.delay_ms / 1_000)
    return payment_status(payment.operation_id)


@app.get("/report")
def provider_report() -> dict[str, Any]:
    require_kind("provider")
    control = ProviderControl.model_validate(
        get_control("provider", ProviderControl().model_dump())
    )
    with connect() as connection:
        rows = connection.execute(
            "SELECT * FROM receipts ORDER BY created_at, operation_id"
        ).fetchall()
    receipts = [dict(row) for row in rows]
    if receipts and control.report_discrepancy_minor:
        target = (
            next(
                (row for row in receipts if row["operation_id"] == control.report_operation_id),
                None,
            )
            if control.report_operation_id
            else receipts[0]
        )
        if target is not None:
            target["amount_minor"] += control.report_discrepancy_minor
    return {"provider": SERVICE_NAME, "receipts": receipts}


@app.get("/control")
def read_control() -> dict[str, Any]:
    require_kind("provider")
    return get_control("provider", ProviderControl().model_dump())


@app.post("/control")
def configure_provider(control: ProviderControl) -> dict[str, Any]:
    require_kind("provider")
    save_control("provider", control.model_dump())
    return control.model_dump()


@app.post("/control/fail")
def configure_receiver(control: ReceiverControl) -> dict[str, Any]:
    require_kind("receiver")
    save_control("receiver", control.model_dump())
    return control.model_dump()


@app.get("/control/fail")
def read_receiver_control() -> dict[str, Any]:
    require_kind("receiver")
    return get_control("receiver", ReceiverControl().model_dump())


@app.post("/events")
async def receive_event(
    request: Request,
    event_id: Annotated[str, Header(alias="X-LedgerFlow-Event-ID", min_length=1, max_length=128)],
    timestamp: Annotated[str, Header(alias="X-LedgerFlow-Timestamp", max_length=20)],
    signature: Annotated[
        str, Header(alias="X-LedgerFlow-Signature", pattern=r"^sha256=[0-9a-f]{64}$")
    ],
) -> dict[str, Any]:
    require_kind("receiver")
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_BODY_BYTES:
            raise HTTPException(413, "Sandbox webhook exceeds body limit")
        chunks.append(chunk)
    body = b"".join(chunks)
    try:
        signed_at = int(timestamp)
    except ValueError as error:
        raise HTTPException(401, "Invalid signature timestamp") from error
    if abs(int(time.time()) - signed_at) > 300:
        raise HTTPException(401, "Expired signature timestamp")
    material = timestamp.encode() + b"." + event_id.encode() + b"." + body
    expected = "sha256=" + hmac.new(WEBHOOK_SECRET.encode(), material, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(401, "Invalid webhook signature")
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError) as error:
        raise HTTPException(400, "Invalid JSON event") from error
    if not isinstance(payload, dict) or payload.get("event_id", event_id) != event_id:
        raise HTTPException(400, "Event ID does not match signed header")
    if get_control("receiver", ReceiverControl().model_dump())["fail"]:
        raise HTTPException(500, "Sandbox receiver failure enabled")
    body_hash = hashlib.sha256(body).hexdigest()
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        prior = connection.execute(
            "SELECT body_hash FROM received_events WHERE event_id = ?", (event_id,)
        ).fetchone()
        if prior and prior["body_hash"] != body_hash:
            raise HTTPException(409, "Event ID payload conflict")
        connection.execute(
            "INSERT INTO received_events(event_id, body_hash, payload, received_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(event_id) DO UPDATE SET deliveries = deliveries + 1",
            (event_id, body_hash, body.decode(), int(time.time())),
        )
    return {"accepted": True, "event_id": event_id, "duplicate": prior is not None}


@app.get("/events")
def received_events() -> dict[str, Any]:
    require_kind("receiver")
    with connect() as connection:
        rows = connection.execute(
            "SELECT event_id, received_at, deliveries FROM received_events ORDER BY received_at"
        ).fetchall()
    return {"events": [dict(row) for row in rows]}
