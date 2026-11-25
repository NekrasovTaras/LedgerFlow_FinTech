import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.core.config import get_settings
from app.core.errors import AppError
from app.infrastructure.database import SessionLocal
from app.infrastructure.models import ChaosExperiment, User, new_id
from app.modules.chaos import application
from app.modules.chaos.presentation import router
from app.modules.chaos.scenarios import SCENARIOS


@pytest.fixture
async def chaos_admin(migrated_database) -> User:
    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.email == "admin@ledgerflow.local"))
        assert user is not None
        return user


@pytest.mark.integration
@pytest.mark.parametrize("scenario_id", list(SCENARIOS))
async def test_real_bounded_experiment_records_actual_outcomes(chaos_admin, scenario_id):
    started = await application.start_experiment(chaos_admin, scenario_id)
    await application.wait_for_completion(started["id"])
    result = await application.get_experiment(chaos_admin, started["id"])
    assert result["status"] == "PASSED", result
    assert result["result"]["passed"] is True
    assert result["invariants"]["valid"] is True
    assert result["completed_at"]
    assert result["timeline"][0]["step"] == "experiment_accepted"
    assert result["timeline"][-1]["step"] == "experiment_completed"
    assert len(result["timeline"]) >= 4
    assert result["recovery"] == SCENARIOS[scenario_id].recovery
    if scenario_id == "duplicate_requests":
        assert result["result"]["commands"] == 100
        assert result["result"]["capture_journals"] == 1
        assert result["result"]["entrypoint"] == "application"
    if scenario_id == "concurrent_debits":
        assert result["result"]["accepted"] == 10
        assert result["result"]["insufficient_funds"] == 10
        assert result["result"]["captured"] == 10
    if scenario_id == "worker_before_ack":
        assert result["result"]["redelivered"] is True
        assert result["result"]["inbox_records"] == 1
        assert result["result"]["webhook_deliveries"] == 1
    if scenario_id == "reconciliation_discrepancy":
        assert result["result"]["ledger_balance_unchanged"] is True


@pytest.mark.parametrize("role", ["customer", "merchant", "risk_analyst"])
def test_chaos_denies_non_operational_roles(role):
    actor = User(role=role)
    with pytest.raises(AppError) as caught:
        application.authorized(actor)
    assert caught.value.status == 403


def test_chaos_requires_explicit_sandbox(monkeypatch):
    monkeypatch.setattr(get_settings(), "sandbox_mode", False)
    with pytest.raises(AppError) as caught:
        application.authorized(User(role="administrator"))
    assert caught.value.code == "SANDBOX_DISABLED"


async def test_unknown_scenario_rejected_before_any_experiment(chaos_admin):
    with pytest.raises(AppError) as caught:
        await application.start_experiment(chaos_admin, "not-a-scenario")
    assert caught.value.code == "UNKNOWN_SCENARIO"


async def test_only_one_experiment_can_own_the_database_lock(chaos_admin):
    connection = await application._acquire_lock()
    assert connection is not None
    try:
        with pytest.raises(AppError) as caught:
            await application.start_experiment(chaos_admin, "broker_outage")
        assert caught.value.code == "CHAOS_BUSY"
    finally:
        await application._release_lock(connection)


async def test_failed_runner_is_persisted_as_failure(chaos_admin, monkeypatch):
    async def broken(_: application.ExperimentContext) -> dict[str, Any]:
        raise RuntimeError("This marker must not be echoed as a fabricated successful result.")

    monkeypatch.setitem(application.RUNNERS, "broker_outage", broken)
    started = await application.start_experiment(chaos_admin, "broker_outage")
    await application.wait_for_completion(started["id"])
    result = await application.get_experiment(chaos_admin, started["id"])
    assert result["status"] == "FAILED"
    assert result["result"] == {"passed": False, "error": "RuntimeError"}
    assert result["invariants"]["valid"] is True


async def test_shutdown_interrupts_runner_and_releases_lock(chaos_admin, monkeypatch):
    async def waiting(_: application.ExperimentContext) -> dict[str, Any]:
        await asyncio.Event().wait()
        raise AssertionError("Unreachable")

    monkeypatch.setitem(application.RUNNERS, "broker_outage", waiting)
    started = await application.start_experiment(chaos_admin, "broker_outage")
    await application.shutdown_experiments()
    result = await application.get_experiment(chaos_admin, started["id"])
    assert result["status"] == "INTERRUPTED"
    assert result["result"]["passed"] is False
    connection = await application._acquire_lock()
    assert connection is not None
    await application._release_lock(connection)


async def test_stale_runner_is_recovered_without_claiming_success(chaos_admin):
    identifier = new_id()
    async with SessionLocal.begin() as session:
        session.add(
            ChaosExperiment(
                id=identifier,
                actor_id=chaos_admin.id,
                scenario_id="broker_outage",
                status="RUNNING",
                expected_behavior="Durable event replay",
                recovery="Inspect the persisted audit trail.",
                created_at=datetime.now(UTC) - timedelta(minutes=3),
                result={},
                invariants={},
                timeline=[],
            )
        )
    assert await application.recover_stale_experiments() >= 1
    result = await application.get_experiment(chaos_admin, identifier)
    assert result["status"] == "INTERRUPTED"
    assert result["result"]["passed"] is False
    assert result["invariants"]["valid"] is True


async def test_router_contract_and_real_authentication(client):
    isolated_app = FastAPI()
    isolated_app.include_router(router)

    @isolated_app.exception_handler(AppError)
    async def error_response(request: Request, error: AppError) -> JSONResponse:
        return JSONResponse(error.payload(), error.status)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=isolated_app),
        base_url="http://testserver",
    ) as endpoint:
        unauthenticated = await endpoint.get("/api/v1/chaos/scenarios")
        assert unauthenticated.status_code == 401
        login = await client.post(
            "/api/v1/auth/login",
            json={
                "email": "admin@ledgerflow.local",
                "password": "DemoPass!2026",
            },
        )
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        listing = await endpoint.get("/api/v1/chaos/scenarios", headers=headers)
        assert listing.status_code == 200
        assert listing.json()["total"] == len(listing.json()["items"]) == 10
        rejected = await endpoint.post(
            "/api/v1/chaos/experiments", headers=headers, json={"scenario_id": "unknown"}
        )
        assert rejected.status_code == 422
        bad_id = await endpoint.get("/api/v1/chaos/experiments/not-a-uuid", headers=headers)
        assert bad_id.status_code == 422
