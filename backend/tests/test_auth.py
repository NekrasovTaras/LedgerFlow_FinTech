"""Behavioral security tests; database fixtures target disposable PostgreSQL."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import jwt
import pytest
from sqlalchemy import func, select, update

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.security import decode_access_token, hash_password, token_digest, verify_password
from app.infrastructure.database import SessionLocal
from app.infrastructure.models import AuthSession, LoginAttempt, User
from app.modules.auth.router import REFRESH_COOKIE

PASSWORD = "SyntheticPass!2026"


async def register_user(client: httpx.AsyncClient) -> dict[str, str]:
    email = f"auth-{uuid4().hex}@example.test"
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "display_name": "Synthetic customer"},
    )
    assert response.status_code == 201
    return {"email": email, "id": response.json()["id"]}


async def login_user(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_passwords_use_argon2id_and_are_salted() -> None:
    encoded = hash_password(PASSWORD)
    assert encoded.startswith("$argon2id$")
    assert encoded != hash_password(PASSWORD)
    assert verify_password(PASSWORD, encoded)
    assert not verify_password("wrong-password", encoded)
    assert not verify_password(PASSWORD, "not-a-password-hash")


def test_invalid_access_token_is_rejected() -> None:
    with pytest.raises(AppError) as error:
        decode_access_token("invalid.token.payload")
    assert error.value.status == 401


@pytest.mark.parametrize("invalid_claim", ["issuer", "expiry", "type", "session_type", "algorithm"])
def test_access_token_contract_is_enforced(invalid_claim: str) -> None:
    settings = get_settings()
    claims: dict[str, object] = {
        "iss": settings.jwt_issuer,
        "sub": str(uuid4()),
        "sid": str(uuid4()),
        "jti": str(uuid4()),
        "iat": datetime.now(UTC),
        "exp": datetime.now(UTC) + timedelta(minutes=1),
        "typ": "access",
    }
    algorithm = "HS256"
    if invalid_claim == "issuer":
        claims["iss"] = "another-issuer"
    elif invalid_claim == "expiry":
        del claims["exp"]
    elif invalid_claim == "type":
        claims["typ"] = "refresh"
    elif invalid_claim == "session_type":
        claims["sid"] = 100
    elif invalid_claim == "algorithm":
        algorithm = "HS384"
    token = jwt.encode(claims, settings.jwt_secret, algorithm=algorithm)
    with pytest.raises(AppError) as error:
        decode_access_token(token)
    assert error.value.status == 401


async def test_registration_forbids_role_escalation(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": f"auth-{uuid4().hex}@example.test",
            "password": PASSWORD,
            "display_name": "Synthetic",
            "role": "administrator",
        },
    )
    assert response.status_code == 422
    assert PASSWORD not in response.text


async def test_registration_stores_hash_and_normalizes_email(client: httpx.AsyncClient) -> None:
    email = f"Auth-{uuid4().hex}@Example.Test"
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": f" {email} ", "password": PASSWORD, "display_name": " Synthetic "},
    )
    assert response.status_code == 201
    assert response.json()["email"] == email.lower()
    assert response.json()["role"] == "customer"
    assert "password_hash" not in response.json()
    async with SessionLocal() as session:
        user = await session.get(User, response.json()["id"])
        assert user is not None
        assert user.password_hash.startswith("$argon2id$")
        assert verify_password(PASSWORD, user.password_hash)


@pytest.mark.parametrize("email", ["not-email", "a..b@example.test", ".a@example.test"])
async def test_registration_rejects_invalid_email(client: httpx.AsyncClient, email: str) -> None:
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "display_name": "Synthetic"},
    )
    assert response.status_code == 422
    assert PASSWORD not in response.text


async def test_duplicate_registration_is_a_conflict(client: httpx.AsyncClient) -> None:
    user = await register_user(client)
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": user["email"], "password": PASSWORD, "display_name": "Duplicate"},
    )
    assert response.status_code == 409


async def test_login_cookie_and_access_control(client: httpx.AsyncClient) -> None:
    user = await register_user(client)
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    response = await client.post(
        "/api/v1/auth/login", json={"email": user["email"], "password": PASSWORD}
    )
    assert response.status_code == 200
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert "path=/api/v1/auth" in cookie
    assert "refresh_token" not in response.json()
    assert response.headers["cache-control"] == "no-store"
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    me = await client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["id"] == user["id"]
    assert "password_hash" not in me.json()
    async with SessionLocal() as session:
        row = await session.scalar(select(AuthSession).where(AuthSession.user_id == user["id"]))
        assert row is not None
        assert row.token_hash == token_digest(client.cookies[REFRESH_COOKIE])


async def test_refresh_rotates_and_revokes_old_access(client: httpx.AsyncClient) -> None:
    user = await register_user(client)
    headers = await login_user(client, user["email"])
    old_cookie = client.cookies[REFRESH_COOKIE]
    response = await client.post("/api/v1/auth/refresh")
    assert response.status_code == 200
    assert client.cookies[REFRESH_COOKIE] != old_cookie
    assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 401
    new_headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    assert (await client.get("/api/v1/auth/me", headers=new_headers)).status_code == 200


async def test_refresh_reuse_persists_family_revocation(client: httpx.AsyncClient) -> None:
    user = await register_user(client)
    await login_user(client, user["email"])
    original = client.cookies[REFRESH_COOKIE]
    rotated = await client.post("/api/v1/auth/refresh")
    assert rotated.status_code == 200
    new_cookie = client.cookies[REFRESH_COOKIE]
    access = {"Authorization": f"Bearer {rotated.json()['access_token']}"}
    replay = await client.post(
        "/api/v1/auth/refresh", headers={"Cookie": f"{REFRESH_COOKIE}={original}"}
    )
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "REFRESH_REUSED"
    assert (await client.get("/api/v1/auth/me", headers=access)).status_code == 401
    rejected = await client.post(
        "/api/v1/auth/refresh", headers={"Cookie": f"{REFRESH_COOKIE}={new_cookie}"}
    )
    assert rejected.status_code == 401
    async with SessionLocal() as session:
        active = await session.scalar(
            select(func.count())
            .select_from(AuthSession)
            .where(AuthSession.user_id == user["id"], AuthSession.revoked_at.is_(None))
        )
    assert active == 0


async def test_logout_revokes_access_and_refresh(client: httpx.AsyncClient) -> None:
    user = await register_user(client)
    headers = await login_user(client, user["email"])
    original = client.cookies[REFRESH_COOKIE]
    response = await client.post("/api/v1/auth/logout", headers=headers)
    assert response.status_code == 200
    assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 401
    assert (
        await client.post(
            "/api/v1/auth/refresh", headers={"Cookie": f"{REFRESH_COOKIE}={original}"}
        )
    ).status_code == 401


async def test_bearer_only_logout_revokes_session(client: httpx.AsyncClient) -> None:
    user = await register_user(client)
    headers = await login_user(client, user["email"])
    client.cookies.clear()
    assert (await client.post("/api/v1/auth/logout", headers=headers)).status_code == 200
    assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 401


async def test_session_management_is_owner_scoped(client: httpx.AsyncClient) -> None:
    first = await register_user(client)
    first_headers = await login_user(client, first["email"])
    listing = await client.get("/api/v1/auth/sessions", headers=first_headers)
    assert listing.status_code == 200
    row = listing.json()["items"][0]
    assert "token_hash" not in row and "family_id" not in row
    second = await register_user(client)
    second_headers = await login_user(client, second["email"])
    assert (
        await client.delete(f"/api/v1/auth/sessions/{row['id']}", headers=second_headers)
    ).status_code == 404
    assert (
        await client.delete(f"/api/v1/auth/sessions/{row['id']}", headers=first_headers)
    ).status_code == 200
    assert (await client.get("/api/v1/auth/me", headers=first_headers)).status_code == 401


async def test_login_throttle_survives_failed_request_transactions(
    client: httpx.AsyncClient,
) -> None:
    user = await register_user(client)
    for _ in range(5):
        failed = await client.post(
            "/api/v1/auth/login", json={"email": user["email"], "password": "wrong-password"}
        )
        assert failed.status_code == 401
    throttled = await client.post(
        "/api/v1/auth/login", json={"email": user["email"], "password": PASSWORD}
    )
    assert throttled.status_code == 429
    assert throttled.json()["error"]["code"] == "LOGIN_THROTTLED"
    async with SessionLocal() as session:
        recent = await session.scalar(select(func.count()).select_from(LoginAttempt))
    assert recent is not None and recent >= 5


async def test_unknown_and_disabled_accounts_return_generic_login_error(
    client: httpx.AsyncClient,
) -> None:
    user = await register_user(client)
    async with SessionLocal.begin() as session:
        await session.execute(update(User).where(User.id == user["id"]).values(active=False))
    disabled = await client.post(
        "/api/v1/auth/login", json={"email": user["email"], "password": PASSWORD}
    )
    unknown = await client.post(
        "/api/v1/auth/login",
        json={"email": f"missing-{uuid4().hex}@example.test", "password": PASSWORD},
    )
    assert disabled.status_code == unknown.status_code == 401
    assert disabled.json()["error"] == unknown.json()["error"]


async def test_disabling_user_revokes_effective_access(client: httpx.AsyncClient) -> None:
    user = await register_user(client)
    headers = await login_user(client, user["email"])
    async with SessionLocal.begin() as session:
        await session.execute(update(User).where(User.id == user["id"]).values(active=False))
    assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 401
    assert (await client.post("/api/v1/auth/refresh")).status_code == 401


async def test_cross_site_cookie_mutations_are_rejected(client: httpx.AsyncClient) -> None:
    user = await register_user(client)
    headers = await login_user(client, user["email"])
    blocked = await client.post("/api/v1/auth/refresh", headers={"Origin": "https://evil.test"})
    assert blocked.status_code == 403
    blocked = await client.post("/api/v1/auth/logout", headers={"Sec-Fetch-Site": "cross-site"})
    assert blocked.status_code == 403
    assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 200


async def test_expired_access_token_is_rejected(client: httpx.AsyncClient) -> None:
    user = await register_user(client)
    headers = await login_user(client, user["email"])
    original = decode_access_token(headers["Authorization"].removeprefix("Bearer "))
    original["exp"] = datetime.now(UTC) - timedelta(seconds=1)
    expired = jwt.encode(original, get_settings().jwt_secret, algorithm="HS256")
    assert (
        await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {expired}"})
    ).status_code == 401
