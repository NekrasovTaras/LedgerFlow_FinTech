"""Object authorization and financial HTTP input contracts on the real API."""

from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from test_financial_acceptance import (
    FinancialEnvironment,
    balances,
    bearer_for,
    payment_payload,
)
from test_financial_acceptance import financial_env as financial_env

from app.infrastructure.database import SessionLocal
from app.infrastructure.models import User, Wallet
from app.modules.payments.application import create_payment

pytestmark = pytest.mark.integration


async def test_customer_lists_and_ledger_accounts_are_owner_scoped(
    client: httpx.AsyncClient,
    financial_env: FinancialEnvironment,
) -> None:
    env = financial_env
    headers = await bearer_for(env.actor)
    wallets = await client.get("/api/v1/wallets", headers=headers)
    assert wallets.status_code == 200
    assert {item["id"] for item in wallets.json()["items"]} == {env.source.id}
    accounts = await client.get("/api/v1/ledger/accounts", headers=headers)
    assert accounts.status_code == 200
    assert {item["owner_id"] for item in accounts.json()["items"]} == {env.actor.id}
    assert {item["id"] for item in accounts.json()["items"]} == {env.source.account_id}
    directory = await client.get("/api/v1/merchants", headers=headers)
    assert directory.status_code == 200
    assert env.merchant_wallet.id in {item["wallet_id"] for item in directory.json()["items"]}
    assert env.target.id not in {item["wallet_id"] for item in directory.json()["items"]}


async def test_foreign_wallet_cannot_be_read_funded_or_spent(
    client: httpx.AsyncClient,
    financial_env: FinancialEnvironment,
) -> None:
    env = financial_env
    headers = {**await bearer_for(env.recipient), "Idempotency-Key": uuid4().hex}
    for path in [
        f"/api/v1/wallets/{env.source.id}",
        f"/api/v1/wallets/{env.source.id}/transactions",
    ]:
        response = await client.get(path, headers=headers)
        assert response.status_code == 404
        assert env.actor.email not in response.text
    funded = await client.post(
        f"/api/v1/wallets/{env.source.id}/sandbox-topup",
        json={"amount_minor": 1_000},
        headers=headers,
    )
    assert funded.status_code == 404
    transfer = await client.post(
        "/api/v1/transfers",
        json={
            "source_wallet_id": env.source.id,
            "destination_wallet_id": env.target.id,
            "amount_minor": 1_000,
        },
        headers={**headers, "Idempotency-Key": uuid4().hex},
    )
    assert transfer.status_code == 404
    payment = await client.post(
        "/api/v1/payments",
        json=payment_payload(env),
        headers={**headers, "Idempotency-Key": uuid4().hex},
    )
    assert payment.status_code == 404
    source, target, merchant = await balances(env)
    assert (source["available_minor"], target["available_minor"], merchant["available_minor"]) == (
        100_000,
        0,
        0,
    )


async def test_unrelated_customer_cannot_inspect_or_mutate_payment(
    client: httpx.AsyncClient,
    financial_env: FinancialEnvironment,
) -> None:
    env = financial_env
    async with SessionLocal.begin() as session:
        payment = await create_payment(session, env.actor, payment_payload(env))
    headers = await bearer_for(env.recipient)
    for suffix in ["", "/timeline"]:
        result = await client.get(f"/api/v1/payments/{payment['id']}{suffix}", headers=headers)
        assert result.status_code == 404
    for suffix, body in [
        ("cancel", {}),
        ("process", {}),
        ("refund", {"amount_minor": 100}),
        ("resolve", {}),
    ]:
        result = await client.post(
            f"/api/v1/payments/{payment['id']}/{suffix}",
            json=body,
            headers={**headers, "Idempotency-Key": uuid4().hex},
        )
        assert result.status_code == 404
    listed = await client.get("/api/v1/payments", headers=headers)
    assert listed.status_code == 200 and listed.json()["total"] == 0
    source, _, merchant = await balances(env)
    assert (source["posted_minor"], source["reserved_minor"], merchant["posted_minor"]) == (
        100_000,
        10_000,
        0,
    )


@pytest.mark.parametrize("path", ["/api/v1/outbox", "/api/v1/audit", "/api/v1/risk/reviews"])
async def test_customer_cannot_read_privileged_operational_resources(
    client: httpx.AsyncClient,
    financial_env: FinancialEnvironment,
    path: str,
) -> None:
    response = await client.get(path, headers=await bearer_for(financial_env.actor))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


async def test_customer_cannot_block_wallet_even_when_it_is_their_own(
    client: httpx.AsyncClient,
    financial_env: FinancialEnvironment,
) -> None:
    response = await client.post(
        f"/api/v1/wallets/{financial_env.source.id}/block",
        json={},
        headers=await bearer_for(financial_env.actor),
    )
    assert response.status_code == 403


async def test_risk_analyst_read_scope_does_not_grant_financial_mutation(
    client: httpx.AsyncClient,
    financial_env: FinancialEnvironment,
) -> None:
    env = financial_env
    async with SessionLocal.begin() as session:
        analyst = User(
            email=f"analyst-{uuid4().hex}@example.test",
            display_name="Synthetic risk analyst",
            password_hash="not-a-password-login-account",
            role="risk_analyst",
            active=True,
        )
        session.add(analyst)
        await session.flush()
        payment = await create_payment(session, env.actor, payment_payload(env))
    payer_headers = await bearer_for(env.actor)
    hold = await client.post(
        f"/api/v1/wallets/{env.source.id}/holds",
        json={"amount_minor": 100},
        headers={**payer_headers, "Idempotency-Key": uuid4().hex},
    )
    assert hold.status_code == 200
    headers = await bearer_for(analyst)
    assert (
        await client.get(f"/api/v1/payments/{payment['id']}", headers=headers)
    ).status_code == 200
    operations = [
        (f"/api/v1/payments/{payment['id']}/cancel", {}),
        (f"/api/v1/payments/{payment['id']}/refund", {"amount_minor": 100}),
        (f"/api/v1/holds/{hold.json()['id']}/release", {}),
        ("/api/v1/wallets", {"name": "Analyst financial wallet", "currency": "RUB"}),
    ]
    for path, payload in operations:
        result = await client.post(
            path,
            json=payload,
            headers={**headers, "Idempotency-Key": uuid4().hex},
        )
        assert result.status_code == 403, (path, result.status_code, result.text)
    source, _, merchant = await balances(env)
    assert (source["posted_minor"], source["reserved_minor"], merchant["posted_minor"]) == (
        100_000,
        10_100,
        0,
    )


@pytest.mark.parametrize("amount", [0, -1, 10.5, True, "1000", 100_000_000_001])
async def test_financial_api_requires_positive_bounded_integer_minor_units(
    client: httpx.AsyncClient,
    financial_env: FinancialEnvironment,
    amount,
) -> None:
    env = financial_env
    response = await client.post(
        f"/api/v1/wallets/{env.source.id}/sandbox-topup",
        json={"amount_minor": amount},
        headers={**await bearer_for(env.actor), "Idempotency-Key": uuid4().hex},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert (await balances(env))[0]["available_minor"] == 100_000


@pytest.mark.parametrize("key", [None, "", "a" * 201, "contains space"])
async def test_financial_post_requires_valid_idempotency_key(
    client: httpx.AsyncClient,
    financial_env: FinancialEnvironment,
    key,
) -> None:
    env = financial_env
    headers = await bearer_for(env.actor)
    if key is not None:
        headers["Idempotency-Key"] = key
    result = await client.post(
        f"/api/v1/wallets/{env.source.id}/sandbox-topup",
        json={"amount_minor": 100},
        headers=headers,
    )
    assert result.status_code == 422
    assert result.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"


async def test_wallet_creation_rejects_unsupported_currency_and_blank_name(
    client: httpx.AsyncClient,
    financial_env: FinancialEnvironment,
) -> None:
    headers = await bearer_for(financial_env.actor)
    usd = await client.post(
        "/api/v1/wallets", json={"name": "USD", "currency": "USD"}, headers=headers
    )
    assert usd.status_code == 422
    blank = await client.post(
        "/api/v1/wallets", json={"name": "   ", "currency": "RUB"}, headers=headers
    )
    assert blank.status_code == 422


async def test_blocked_wallet_cannot_start_financial_operations(
    client: httpx.AsyncClient,
    financial_env: FinancialEnvironment,
) -> None:
    env = financial_env
    async with SessionLocal.begin() as session:
        wallet = await session.scalar(select(Wallet).where(Wallet.id == env.source.id))
        assert wallet is not None
        wallet.status = "BLOCKED"
    headers = await bearer_for(env.actor)
    operations = [
        (f"/api/v1/wallets/{env.source.id}/sandbox-topup", {"amount_minor": 100}),
        (
            "/api/v1/transfers",
            {
                "source_wallet_id": env.source.id,
                "destination_wallet_id": env.target.id,
                "amount_minor": 100,
            },
        ),
        ("/api/v1/payments", payment_payload(env)),
    ]
    for path, payload in operations:
        result = await client.post(
            path, json=payload, headers={**headers, "Idempotency-Key": uuid4().hex}
        )
        assert result.status_code == 409
        assert result.json()["error"]["code"] == "WALLET_BLOCKED"
    assert (await balances(env))[0]["available_minor"] == 100_000


@pytest.mark.parametrize("path", ["/api/v1/wallets", "/api/v1/payments", "/api/v1/ledger/accounts"])
async def test_financial_resources_require_authentication(
    client: httpx.AsyncClient,
    path: str,
) -> None:
    result = await client.get(path)
    assert result.status_code == 401


async def test_invalid_resource_identifier_is_a_validation_error(
    client: httpx.AsyncClient,
    financial_env: FinancialEnvironment,
) -> None:
    result = await client.get(
        "/api/v1/wallets/not-a-uuid", headers=await bearer_for(financial_env.actor)
    )
    assert result.status_code == 422
