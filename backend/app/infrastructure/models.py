from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def new_id() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class Identified:
    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class User(Identified, Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "role IN ('customer','merchant','risk_analyst','operator','administrator')"
        ),
    )
    email: Mapped[str] = mapped_column(String(254), unique=True)
    display_name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(30), default="customer")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_sandbox: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class AuthSession(Identified, Base):
    __tablename__ = "auth_sessions"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    family_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replaced_by: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    user_agent: Mapped[str] = mapped_column(String(200), default="")


class LoginAttempt(Identified, Base):
    __tablename__ = "login_attempts"
    subject_hash: Mapped[str] = mapped_column(String(64), index=True)
    succeeded: Mapped[bool] = mapped_column(Boolean)


class LedgerAccount(Identified, Base):
    __tablename__ = "ledger_accounts"
    __table_args__ = (
        CheckConstraint("currency = 'RUB'"),
        UniqueConstraint("id", "owner_id", "currency", name="uq_ledger_account_identity"),
        CheckConstraint(
            "(kind IN ('USER_WALLET','MERCHANT_WALLET') AND owner_id IS NOT NULL "
            "AND system_key IS NULL AND NOT allow_overdraft) OR "
            "(kind = 'SYSTEM_FUNDING' AND owner_id IS NULL "
            "AND system_key IS NOT NULL AND system_key = 'sandbox_funding' AND allow_overdraft) OR "
            "(kind = 'SETTLEMENT' AND owner_id IS NULL "
            "AND system_key IS NOT NULL AND system_key = 'settlement' AND NOT allow_overdraft) OR "
            "(kind = 'SYSTEM' AND owner_id IS NULL "
            "AND system_key IS NOT NULL AND NOT allow_overdraft)",
            name="ck_ledger_account_policy",
        ),
    )
    name: Mapped[str] = mapped_column(String(120))
    owner_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    currency: Mapped[str] = mapped_column(String(3), default="RUB")
    allow_overdraft: Mapped[bool] = mapped_column(Boolean, default=False)
    system_key: Mapped[str | None] = mapped_column(String(60), unique=True)


class BalanceProjection(Base):
    __tablename__ = "balance_projections"
    __table_args__ = (
        CheckConstraint("reserved_minor >= 0", name="reserved_nonnegative"),
        CheckConstraint(
            "allow_overdraft OR balance_minor >= reserved_minor", name="available_nonnegative"
        ),
    )
    account_id: Mapped[str] = mapped_column(ForeignKey("ledger_accounts.id"), primary_key=True)
    balance_minor: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    reserved_minor: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    allow_overdraft: Mapped[bool] = mapped_column(Boolean, default=False)


class Wallet(Identified, Base):
    __tablename__ = "wallets"
    __table_args__ = (
        CheckConstraint("currency = 'RUB'"),
        CheckConstraint("status IN ('ACTIVE','BLOCKED')"),
        ForeignKeyConstraint(
            ["account_id", "owner_id", "currency"],
            ["ledger_accounts.id", "ledger_accounts.owner_id", "ledger_accounts.currency"],
            name="fk_wallet_account_identity",
        ),
    )
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("ledger_accounts.id"), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    currency: Mapped[str] = mapped_column(String(3), default="RUB")
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")


class JournalEntry(Identified, Base):
    __tablename__ = "journal_entries"
    __table_args__ = (CheckConstraint("currency = 'RUB'"),)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    reference_id: Mapped[str] = mapped_column(String(100), unique=True)
    currency: Mapped[str] = mapped_column(String(3), default="RUB")
    description: Mapped[str] = mapped_column(String(300), default="")
    created_txid: Mapped[int] = mapped_column(BigInteger, server_default=text("txid_current()"))


class Posting(Identified, Base):
    __tablename__ = "postings"
    __table_args__ = (
        CheckConstraint("amount_minor > 0"),
        CheckConstraint("side IN ('DEBIT','CREDIT')"),
    )
    journal_id: Mapped[str] = mapped_column(ForeignKey("journal_entries.id"), index=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("ledger_accounts.id"), index=True)
    side: Mapped[str] = mapped_column(String(6))
    amount_minor: Mapped[int] = mapped_column(BigInteger)


class Hold(Identified, Base):
    __tablename__ = "holds"
    __table_args__ = (
        CheckConstraint("amount_minor > 0"),
        CheckConstraint("status IN ('ACTIVE','CAPTURED','RELEASED')"),
    )
    account_id: Mapped[str] = mapped_column(ForeignKey("ledger_accounts.id"), index=True)
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    reference_id: Mapped[str] = mapped_column(String(100), unique=True)


class Transfer(Identified, Base):
    __tablename__ = "transfers"
    __table_args__ = (CheckConstraint("amount_minor > 0"),)
    source_wallet_id: Mapped[str] = mapped_column(ForeignKey("wallets.id"), index=True)
    destination_wallet_id: Mapped[str] = mapped_column(ForeignKey("wallets.id"), index=True)
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), default="RUB")
    description: Mapped[str] = mapped_column(String(300), default="")
    journal_id: Mapped[str] = mapped_column(ForeignKey("journal_entries.id"))


class Payment(Identified, Base):
    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint("amount_minor > 0"),
        CheckConstraint("refunded_minor >= 0 AND refunded_minor <= amount_minor"),
        CheckConstraint(
            "status IN ('CREATED','PENDING','REQUIRES_REVIEW','AUTHORIZED','PROCESSING',"
            "'UNKNOWN','SUCCEEDED','FAILED','CANCELED','PARTIALLY_REFUNDED','REFUNDED')"
        ),
        Index("ix_payments_wallet_created", "wallet_id", "created_at"),
    )
    wallet_id: Mapped[str] = mapped_column(ForeignKey("wallets.id"), index=True)
    merchant_wallet_id: Mapped[str] = mapped_column(ForeignKey("wallets.id"))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), default="RUB")
    description: Mapped[str] = mapped_column(String(300), default="")
    status: Mapped[str] = mapped_column(String(30), default="CREATED", index=True)
    hold_id: Mapped[str | None] = mapped_column(ForeignKey("holds.id"))
    provider: Mapped[str | None] = mapped_column(String(30))
    provider_reference: Mapped[str | None] = mapped_column(String(100))
    provider_behavior: Mapped[str] = mapped_column(String(30), default="success")
    risk_score: Mapped[int] = mapped_column(Integer, default=0)
    risk_decision: Mapped[str] = mapped_column(String(20), default="ALLOW")
    refunded_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    version: Mapped[int] = mapped_column(Integer, default=1)
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Refund(Identified, Base):
    __tablename__ = "refunds"
    __table_args__ = (CheckConstraint("amount_minor > 0"),)
    payment_id: Mapped[str] = mapped_column(ForeignKey("payments.id"), index=True)
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    journal_id: Mapped[str] = mapped_column(ForeignKey("journal_entries.id"))
    status: Mapped[str] = mapped_column(String(20), default="SUCCEEDED")


class IdempotencyRecord(Identified, Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint("actor_id", "operation", "key", name="uq_idempotency_scope"),
    )
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    operation: Mapped[str] = mapped_column(String(160))
    key: Mapped[str] = mapped_column(String(200))
    fingerprint: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status_code: Mapped[int | None] = mapped_column(Integer)


class TimelineEvent(Identified, Base):
    __tablename__ = "timeline_events"
    payment_id: Mapped[str | None] = mapped_column(ForeignKey("payments.id"), index=True)
    kind: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(160))
    detail: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(40), default="INFO")
    correlation_id: Mapped[str] = mapped_column(String(80))
    trace_id: Mapped[str] = mapped_column(String(32), default="")
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class AuditEvent(Identified, Base):
    __tablename__ = "audit_events"
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(100))
    resource_id: Mapped[str] = mapped_column(String(100), default="")
    correlation_id: Mapped[str] = mapped_column(String(80))
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class RiskReview(Identified, Base):
    __tablename__ = "risk_reviews"
    payment_id: Mapped[str] = mapped_column(ForeignKey("payments.id"), unique=True)
    score: Mapped[int] = mapped_column(Integer)
    rules: Mapped[list[str]] = mapped_column(JSONB)
    explanation: Mapped[str] = mapped_column(Text)
    rules_version: Mapped[str] = mapped_column(String(30), default="2026-11-v1")
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    reviewer_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[str | None] = mapped_column(String(500))


class OutboxEvent(Identified, Base):
    __tablename__ = "outbox_events"
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), default="PENDING", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(500))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InboxEvent(Identified, Base):
    __tablename__ = "inbox_events"
    __table_args__ = (UniqueConstraint("event_id", "consumer", name="uq_inbox_consumer"),)
    event_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    consumer: Mapped[str] = mapped_column(String(60))


class WebhookSubscription(Identified, Base):
    __tablename__ = "webhook_subscriptions"
    __table_args__ = (
        UniqueConstraint("owner_id", "receiver", name="uq_webhook_subscription"),
        CheckConstraint("receiver = 'mock'"),
    )
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    receiver: Mapped[str] = mapped_column(String(30), default="mock")
    event_types: Mapped[list[str]] = mapped_column(JSONB)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class WebhookDelivery(Identified, Base):
    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        UniqueConstraint("event_id", "subscription_id", name="uq_webhook_event_subscription"),
    )
    event_id: Mapped[str] = mapped_column(ForeignKey("outbox_events.id"))
    subscription_id: Mapped[str] = mapped_column(ForeignKey("webhook_subscriptions.id"))
    event_type: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20), default="PENDING", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(500))
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ProviderCircuit(Base):
    __tablename__ = "provider_circuits"
    name: Mapped[str] = mapped_column(String(30), primary_key=True)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    opened_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total_requests: Mapped[int] = mapped_column(Integer, default=0)
    successes: Mapped[int] = mapped_column(Integer, default=0)
    last_latency_ms: Mapped[int] = mapped_column(Integer, default=0)


class ReconciliationRun(Identified, Base):
    __tablename__ = "reconciliation_runs"
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(20), default="RUNNING")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class Discrepancy(Identified, Base):
    __tablename__ = "discrepancies"
    run_id: Mapped[str] = mapped_column(ForeignKey("reconciliation_runs.id"), index=True)
    classification: Mapped[str] = mapped_column(String(60))
    reference_id: Mapped[str] = mapped_column(String(100))
    detail: Mapped[str] = mapped_column(Text)
    recommendation: Mapped[str] = mapped_column(Text)


class ChaosExperiment(Identified, Base):
    __tablename__ = "chaos_experiments"
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    scenario_id: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(20), default="RUNNING")
    expected_behavior: Mapped[str] = mapped_column(Text)
    recovery: Mapped[str] = mapped_column(Text)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    invariants: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    timeline: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
