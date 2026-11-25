from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from prometheus_client import CollectorRegistry, generate_latest
from sqlalchemy import select, text

from app.core.observability import PROVIDER_DURATION
from app.infrastructure.database import SessionLocal
from app.infrastructure.models import Payment, ReconciliationRun, User
from app.modules.ledger.application import audit, create_wallet, topup, wallet_view
from app.modules.operations.metrics import (
    BusinessMetricsCollector,
    BusinessSnapshot,
    business_metrics,
    read_business_snapshot,
    refresh_business_metrics,
)
from app.modules.payments.application import create_payment, finalize, transition
from app.modules.providers import application as providers


@pytest.mark.integration
async def test_metrics_only_publish_committed_audit_facts(migrated_database):
    await refresh_business_metrics()
    before = business_metrics.snapshot.webhook_failures
    async with SessionLocal() as writer:
        audit(
            writer,
            None,
            "webhook.attempt",
            str(uuid4()),
            {"attempt": 1, "status": "RETRY", "error": "Synthetic metrics test failure"},
        )
        await writer.flush()
        await refresh_business_metrics()
        assert business_metrics.snapshot.webhook_failures == before
        await writer.rollback()
    await refresh_business_metrics()
    assert business_metrics.snapshot.webhook_failures == before
    async with SessionLocal.begin() as writer:
        # Retained append-only test history, never a call to a provider/receiver.
        audit(
            writer,
            None,
            "webhook.attempt",
            str(uuid4()),
            {"attempt": 1, "status": "RETRY", "error": "Synthetic committed metrics test"},
        )
    await refresh_business_metrics()
    assert business_metrics.snapshot.webhook_failures == before + 1


async def test_api_scrape_exposes_database_business_metrics(client):
    response = await client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert 'ledgerflow_payments_total{status="SUCCEEDED"}' in response.text
    assert "ledgerflow_payment_processing_duration_seconds_count " in response.text
    assert 'ledgerflow_provider_submission_requests_total{provider="alpha"}' in response.text
    assert "ledgerflow_webhook_failures_total " in response.text
    assert "payment_id=" not in response.text
    assert "user_id=" not in response.text


@pytest.mark.integration
async def test_payment_timing_and_background_results_use_real_database_facts(migrated_database):
    async with SessionLocal() as session:
        baseline = await read_business_snapshot(session)
        actor = User(
            email=f"metrics-{uuid4()}@example.test",
            display_name="Metrics rollback fixture",
            password_hash="not-a-login-account",
            role="customer",
            active=True,
        )
        session.add(actor)
        await session.flush()
        merchant = await session.scalar(
            select(User).where(User.email == "merchant@ledgerflow.local")
        )
        assert merchant is not None
        source = await create_wallet(session, actor.id, "Metrics source")
        destination = await create_wallet(session, merchant.id, "Metrics merchant")
        await topup(session, actor, source.id, 10000)
        result = await create_payment(
            session,
            actor,
            {"wallet_id": source.id, "merchant_wallet_id": destination.id, "amount_minor": 1000},
            bypass_risk=True,
        )
        payment = await session.get(Payment, result["id"])
        assert payment is not None
        payment.processing_started_at = datetime.now(UTC) - timedelta(seconds=2)
        transition(session, payment, "PROCESSING")
        await finalize(session, payment, {"status": "SUCCEEDED", "provider_reference": "fixture"})
        session.add(
            ReconciliationRun(
                status="DISCREPANCIES",
                completed_at=datetime.now(UTC) + timedelta(seconds=1),
                summary={"discrepancy_count": 3, "invariants": {"valid": False}},
            )
        )
        await session.flush()
        await session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        snapshot = await read_business_snapshot(session)
        assert snapshot.outcomes["SUCCEEDED"] == baseline.outcomes.get("SUCCEEDED", 0) + 1
        assert snapshot.timing_counts[-1] == baseline.timing_counts[-1] + 1
        assert snapshot.timing_counts[0] == baseline.timing_counts[0]
        assert 2 <= snapshot.timing_sum - baseline.timing_sum < 5
        assert snapshot.discrepancies == 3
        assert snapshot.invariant_failures == baseline.invariant_failures + 1
        assert snapshot.outbox_backlog > baseline.outbox_backlog
        assert (await wallet_view(session, source))["available_minor"] == 9000
        assert (await wallet_view(session, destination))["posted_minor"] == 1000
        await session.rollback()
    await refresh_business_metrics()
    assert business_metrics.snapshot.outcomes == baseline.outcomes
    assert business_metrics.snapshot.timing_counts == baseline.timing_counts


def test_metric_exposition_has_bounded_labels_and_cumulative_histogram():
    collector = BusinessMetricsCollector()
    collector.snapshot = BusinessSnapshot(
        outcomes={"SUCCEEDED": 2, "FAILED": 1},
        outbox_backlog=4,
        webhook_failures=3,
        discrepancies=1,
        timing_counts=(0, 0, 0, 1, 2, 2, 2, 2, 2, 2, 2),
        timing_sum=2.4,
        providers=(("alpha", 3, 2, 125),),
    )
    registry = CollectorRegistry()
    registry.register(collector)
    exposition = generate_latest(registry).decode()
    assert 'ledgerflow_payments_total{status="SUCCEEDED"} 2.0' in exposition
    assert 'ledgerflow_provider_last_response_seconds{provider="alpha"} 0.125' in exposition
    assert 'ledgerflow_payment_processing_duration_seconds_bucket{le="+Inf"} 2.0' in exposition
    assert "ledgerflow_payment_processing_duration_seconds_sum 2.4" in exposition
    for metric in collector.collect():
        for sample in metric.samples:
            assert set(sample.labels) <= {"status", "provider", "le"}


def provider_sample_count():
    return next(
        sample.value
        for metric in PROVIDER_DURATION.collect()
        for sample in metric.samples
        if sample.name == "ledgerflow_provider_response_seconds_count"
        and sample.labels == {"provider": "alpha"}
    )


@pytest.mark.parametrize(
    "operation",
    ["submit_success", "submit_timeout", "submit_unavailable", "lookup_timeout", "lookup_missing"],
)
async def test_provider_histogram_records_actual_adapter_calls_including_errors(
    monkeypatch, operation
):
    async def handler(request):
        if operation.endswith("timeout"):
            raise httpx.ReadTimeout("Synthetic transport timeout", request=request)
        if operation == "lookup_missing":
            return httpx.Response(200, json={"status": "NOT_FOUND"})
        if operation == "submit_unavailable":
            return httpx.Response(503, json={"detail": {"no_effect": True}})
        return httpx.Response(
            200,
            json={
                "operation_id": "metrics-operation",
                "amount_minor": 1000,
                "currency": "RUB",
                "status": "SUCCEEDED",
                "provider_reference": "alpha-metrics",
            },
        )

    async def record_result(*args):
        # Instrumentation unit test; no financial PostgreSQL behavior is mocked.
        return None

    client_type = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        providers.httpx, "AsyncClient", lambda **kwargs: client_type(transport=transport, **kwargs)
    )
    monkeypatch.setattr(providers, "record_result", record_result)
    PROVIDER_DURATION.labels(provider="alpha")
    before = provider_sample_count()
    if operation.startswith("submit"):
        result = await providers.submit("alpha", "metrics-operation", 1000, "success")
    else:
        result = await providers.lookup("alpha", "metrics-operation", 1000)
    expected = (
        "UNKNOWN"
        if operation.endswith("timeout")
        else "UNAVAILABLE"
        if operation == "submit_unavailable"
        else "NOT_FOUND"
        if operation == "lookup_missing"
        else "SUCCEEDED"
    )
    assert result["status"] == expected
    assert provider_sample_count() == before + (2 if operation == "submit_unavailable" else 1)
