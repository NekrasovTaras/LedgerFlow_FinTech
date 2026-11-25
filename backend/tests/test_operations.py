from uuid import uuid4

import pytest


@pytest.fixture
async def admin_headers(client):
    response = await client.post(
        "/api/v1/auth/login", json={"email": "admin@ledgerflow.local", "password": "DemoPass!2026"}
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.mark.parametrize(
    "path",
    [
        "dashboard",
        "providers",
        "settings",
        "system/health",
        "reconciliation/runs",
        "ledger/accounts",
        "ledger/journal",
        "outbox",
        "audit",
    ],
)
async def test_operation_screen_contracts(client, admin_headers, path):
    response = await client.get(f"/api/v1/{path}", headers=admin_headers)
    assert response.status_code == 200, response.text
    data = response.json()
    if path == "dashboard":
        assert type(data["available_minor"]) is int
        assert data["total_payments"] >= 0
        assert isinstance(data["volume_series"], list)
    elif path == "system/health":
        assert data["services"]["postgres"] == "healthy"
        assert data["sandbox_mode"] is True
    elif path == "settings":
        assert data["currency"] == "RUB"
    else:
        assert isinstance(data["items"], list)
        assert data["total"] >= len(data["items"])


async def test_reconciliation_run_detail_csv_are_actual_records(client, admin_headers):
    response = await client.post("/api/v1/reconciliation/runs", headers=admin_headers, json={})
    assert response.status_code == 200, response.text
    run = response.json()
    assert run["status"] in {"COMPLETED", "DISCREPANCIES"}
    assert run["summary"]["invariants"]["valid"] is True
    detail = await client.get(f"/api/v1/reconciliation/runs/{run['id']}", headers=admin_headers)
    assert detail.status_code == 200
    assert detail.json()["discrepancy_count"] == len(detail.json()["discrepancies"])
    csv = await client.get(f"/api/v1/reconciliation/runs/{run['id']}/export", headers=admin_headers)
    assert csv.status_code == 200
    assert "text/csv" in csv.headers["content-type"]
    assert csv.text.startswith("classification,reference_id,detail,recommendation")


async def test_manual_hold_history_and_missing_run(client, admin_headers):
    created = await client.post(
        "/api/v1/wallets", headers=admin_headers, json={"name": "History test"}
    )
    wallet_id = created.json()["id"]
    listed = await client.get(f"/api/v1/wallets/{wallet_id}/holds", headers=admin_headers)
    assert listed.status_code == 200 and listed.json()["items"] == []
    missing = await client.get(f"/api/v1/reconciliation/runs/{uuid4()}", headers=admin_headers)
    assert missing.status_code == 404
