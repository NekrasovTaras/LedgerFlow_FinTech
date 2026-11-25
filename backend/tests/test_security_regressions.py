from uuid import uuid4

import pytest

from app.bootstrap import validate_runtime_mode
from app.core.config import get_settings
from app.modules.providers.application import validate_receipt


@pytest.mark.parametrize(
    "field,value",
    [
        ("operation_id", "different-operation"),
        ("amount_minor", 999),
        ("amount_minor", 1000.0),
        ("amount_minor", True),
        ("currency", "USD"),
        ("fee_minor", 1001),
        ("provider_reference", ""),
    ],
)
def test_provider_receipt_must_match_exact_operation(field, value):
    data = {
        "operation_id": "expected",
        "amount_minor": 1000,
        "currency": "RUB",
        "status": "SUCCEEDED",
        "provider_reference": "alpha-receipt",
        "fee_minor": 2,
    }
    with pytest.raises(ValueError):
        validate_receipt({**data, field: value}, "expected", 1000)


def test_matching_provider_receipt_is_accepted():
    data = {
        "operation_id": "expected",
        "amount_minor": 1000,
        "currency": "RUB",
        "status": "SUCCEEDED",
        "provider_reference": "alpha-receipt",
    }
    assert validate_receipt(data, "expected", 1000)["amount_minor"] == 1000


async def test_chunked_body_limit_is_enforced_before_parsing(client):
    async def oversized():
        yield b'{"password":"'
        yield b"x" * 600_000
        yield b"x" * 600_000
        yield b'"}'

    response = await client.post(
        "/api/v1/auth/register", content=oversized(), headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "REQUEST_TOO_LARGE"
    assert "xxxxx" not in response.text


async def test_sandbox_identity_and_existing_tokens_denied_outside_sandbox(client, monkeypatch):
    response = await client.post(
        "/api/v1/auth/login", json={"email": "admin@ledgerflow.local", "password": "DemoPass!2026"}
    )
    assert response.status_code == 200
    token = response.json()["access_token"]
    monkeypatch.setattr(get_settings(), "sandbox_mode", False)
    login = await client.post(
        "/api/v1/auth/login", json={"email": "admin@ledgerflow.local", "password": "DemoPass!2026"}
    )
    assert login.status_code == 401
    assert (
        await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    ).status_code == 401
    assert (await client.post("/api/v1/auth/refresh")).status_code == 401
    with pytest.raises(RuntimeError, match="sandbox identities"):
        await validate_runtime_mode()


async def test_analyst_cannot_resolve_financial_effect(client):
    admin = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@ledgerflow.local", "password": "DemoPass!2026"},
    )
    admin_headers = {"Authorization": f"Bearer {admin.json()['access_token']}"}
    wallet = await client.post(
        "/api/v1/wallets", headers=admin_headers, json={"name": "Resolve authorization test"}
    )
    wallet_id = wallet.json()["id"]
    funded = await client.post(
        f"/api/v1/wallets/{wallet_id}/sandbox-topup",
        headers={**admin_headers, "Idempotency-Key": str(uuid4())},
        json={"amount_minor": 2000},
    )
    assert funded.status_code == 200
    directory = await client.get("/api/v1/merchants", headers=admin_headers)
    payment = await client.post(
        "/api/v1/payments",
        headers={**admin_headers, "Idempotency-Key": str(uuid4())},
        json={
            "wallet_id": wallet_id,
            "merchant_wallet_id": directory.json()["items"][0]["wallet_id"],
            "amount_minor": 1000,
            "auto_process": False,
        },
    )
    assert payment.status_code == 200 and payment.json()["status"] == "AUTHORIZED"
    payment_id = payment.json()["id"]
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "analyst@ledgerflow.local", "password": "DemoPass!2026"},
    )
    token = response.json()["access_token"]
    denied = await client.post(
        f"/api/v1/payments/{payment_id}/resolve", headers={"Authorization": f"Bearer {token}"}
    )
    assert denied.status_code == 403
    unchanged = await client.get(f"/api/v1/payments/{payment_id}", headers=admin_headers)
    assert unchanged.json()["status"] == "AUTHORIZED"
    # The role check is object-aware: nonexistent resources still return404.
    missing = await client.post(
        f"/api/v1/payments/{uuid4()}/resolve", headers={"Authorization": f"Bearer {token}"}
    )
    assert missing.status_code == 404
