"""Password and token primitives; refresh credentials are stored only as digests."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import jwt
from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import get_settings
from app.core.errors import AppError

PASSWORD_HASHER = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1, type=Type.ID)


def hash_password(password: str) -> str:
    return PASSWORD_HASHER.hash(password)


DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(32))


def verify_password(password: str, encoded: str) -> bool:
    try:
        return PASSWORD_HASHER.verify(encoded, password)
    except (VerificationError, InvalidHashError):
        return False


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def access_token(user_id: str, session_id: str) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "iss": settings.jwt_issuer,
            "sub": user_id,
            "sid": session_id,
            "jti": str(uuid4()),
            "iat": now,
            "exp": now + timedelta(minutes=settings.access_token_minutes),
            "typ": "access",
        },
        settings.jwt_secret,
        algorithm="HS256",
    )


def decode_access_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        if len(token) > 8192:
            raise ValueError("Oversized token")
        claims = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=["HS256"],
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iat", "iss", "sub", "sid", "jti", "typ"]},
        )
        if claims["typ"] != "access":
            raise ValueError("Wrong token type")
        if not all(isinstance(claims[name], str) for name in ("sub", "sid", "jti")):
            raise ValueError("Token identifiers must be strings")
        UUID(claims["sub"])
        UUID(claims["sid"])
        UUID(claims["jti"])
        return claims
    except (jwt.InvalidTokenError, ValueError, TypeError, KeyError) as error:
        raise AppError("UNAUTHENTICATED", "A valid access token is required.", 401) from error
