"""PostgreSQL-backed authentication, bounded login attempts and session families."""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from anyio import to_thread
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.security import (
    DUMMY_PASSWORD_HASH,
    PASSWORD_HASHER,
    access_token,
    hash_password,
    new_refresh_token,
    token_digest,
    verify_password,
)
from app.infrastructure.database import SessionLocal
from app.infrastructure.models import AuthSession, LoginAttempt, User
from app.modules.ledger.application import audit

LOGIN_WINDOW = timedelta(minutes=5)
MAX_FAILED_LOGINS = 5


@dataclass(frozen=True)
class AuthResult:
    user: User
    session: AuthSession
    refresh_token: str

    def response(self) -> dict[str, Any]:
        return {
            "access_token": access_token(self.user.id, self.session.id),
            "token_type": "bearer",
            "user": public_user(self.user),
        }


def public_user(user: User) -> dict[str, Any]:
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role,
    }


def _lock_key(value: str) -> int:
    return int.from_bytes(hashlib.sha256(value.encode()).digest()[:8], "big", signed=True)


async def _lock_family(session: AsyncSession, family_id: str) -> None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"),
        {"lock_key": _lock_key(f"auth-family:{family_id}")},
    )


async def _new_session(
    session: AsyncSession, user_id: str, user_agent: str, family_id: str | None = None
) -> tuple[AuthSession, str]:
    token = new_refresh_token()
    row = AuthSession(
        id=str(uuid4()),
        user_id=user_id,
        family_id=family_id or str(uuid4()),
        token_hash=token_digest(token),
        expires_at=datetime.now(UTC) + timedelta(days=get_settings().refresh_token_days),
        user_agent="".join(character for character in user_agent if ord(character) >= 32)[:200],
    )
    session.add(row)
    await session.flush()
    return row, token


async def _revoke_family(session: AsyncSession, family_id: str, now: datetime) -> None:
    await session.execute(
        update(AuthSession)
        .where(AuthSession.family_id == family_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )


async def register(email: str, password: str, display_name: str) -> dict[str, Any]:
    encoded = await to_thread.run_sync(hash_password, password)
    try:
        async with SessionLocal.begin() as session:
            user = User(
                email=email,
                display_name=display_name,
                password_hash=encoded,
                role="customer",
                active=True,
                is_sandbox=get_settings().sandbox_mode,
            )
            session.add(user)
            await session.flush()
            audit(session, user.id, "auth.register", user.id)
            result = public_user(user)
    except IntegrityError as error:
        raise AppError("ACCOUNT_UNAVAILABLE", "This email cannot be registered.", 409) from error
    return result


async def login(email: str, password: str, client_ip: str, user_agent: str) -> AuthResult:
    subject_hash = token_digest(f"login:{email}\n{client_ip}")
    now = datetime.now(UTC)
    failure: AppError | None = None
    result: AuthResult | None = None
    async with SessionLocal.begin() as session:
        # Serialize attempts for this subject so parallel failures cannot bypass the limit.
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": _lock_key(f"auth-login:{subject_hash}")},
        )
        failed = await session.scalar(
            select(func.count())
            .select_from(LoginAttempt)
            .where(
                LoginAttempt.subject_hash == subject_hash,
                LoginAttempt.succeeded.is_(False),
                LoginAttempt.created_at >= now - LOGIN_WINDOW,
            )
        )
        if (failed or 0) >= MAX_FAILED_LOGINS:
            failure = AppError("LOGIN_THROTTLED", "Too many failed attempts. Retry later.", 429)
        else:
            user = await session.scalar(select(User).where(User.email == email))
            encoded = user.password_hash if user else DUMMY_PASSWORD_HASH
            matches = await to_thread.run_sync(verify_password, password, encoded)
            authenticated = bool(
                user
                and user.active
                and matches
                and (not user.is_sandbox or get_settings().sandbox_mode)
            )
            session.add(LoginAttempt(subject_hash=subject_hash, succeeded=authenticated))
            if not authenticated:
                failure = AppError("INVALID_CREDENTIALS", "Email or password is incorrect.", 401)
            else:
                assert user is not None
                if PASSWORD_HASHER.check_needs_rehash(user.password_hash):
                    user.password_hash = await to_thread.run_sync(hash_password, password)
                row, token = await _new_session(session, user.id, user_agent)
                audit(session, user.id, "auth.login", row.id)
                result = AuthResult(user, row, token)
    # Raising inside the transaction would roll back the failed-attempt evidence.
    if failure:
        raise failure
    assert result is not None
    return result


async def refresh(token: str, user_agent: str) -> AuthResult:
    if not token or len(token) > 256:
        raise AppError("INVALID_REFRESH", "A valid refresh session is required.", 401)
    failure: AppError | None = None
    result: AuthResult | None = None
    now = datetime.now(UTC)
    async with SessionLocal.begin() as session:
        row = await session.scalar(
            select(AuthSession).where(AuthSession.token_hash == token_digest(token))
        )
        if row is None:
            failure = AppError("INVALID_REFRESH", "A valid refresh session is required.", 401)
        else:
            await _lock_family(session, row.family_id)
            await session.refresh(row, with_for_update=True)
            user = await session.get(User, row.user_id)
            if row.revoked_at is not None:
                await _revoke_family(session, row.family_id, now)
                audit(session, row.user_id, "auth.refresh_reuse", row.family_id)
                failure = AppError("REFRESH_REUSED", "Session reuse detected; sign in again.", 401)
            elif (
                row.expires_at <= now
                or user is None
                or not user.active
                or (user.is_sandbox and not get_settings().sandbox_mode)
            ):
                await _revoke_family(session, row.family_id, now)
                failure = AppError("INVALID_REFRESH", "A valid refresh session is required.", 401)
            else:
                replacement, new_token = await _new_session(
                    session, user.id, user_agent, family_id=row.family_id
                )
                row.revoked_at = now
                row.replaced_by = replacement.id
                audit(session, user.id, "auth.refresh", replacement.id)
                result = AuthResult(user, replacement, new_token)
    # Persist replay detection/family revocation even when returning HTTP 401.
    if failure:
        raise failure
    assert result is not None
    return result


async def revoke_by_token(token: str | None, actor_id: str | None = None) -> None:
    if not token or len(token) > 256:
        return
    async with SessionLocal.begin() as session:
        row = await session.scalar(
            select(AuthSession).where(AuthSession.token_hash == token_digest(token))
        )
        if row is not None and (actor_id is None or row.user_id == actor_id):
            await _lock_family(session, row.family_id)
            await _revoke_family(session, row.family_id, datetime.now(UTC))
            audit(session, row.user_id, "auth.logout", row.family_id)


async def revoke_session(user: User, session_id: str) -> None:
    async with SessionLocal.begin() as session:
        row = await session.get(AuthSession, session_id)
        if row is None or row.user_id != user.id:
            raise AppError("SESSION_NOT_FOUND", "Session not found.", 404)
        await _lock_family(session, row.family_id)
        await _revoke_family(session, row.family_id, datetime.now(UTC))
        audit(session, user.id, "auth.session_revoked", row.family_id)


async def list_sessions(user: User, page: int, page_size: int) -> dict[str, Any]:
    async with SessionLocal() as session:
        criteria = (
            AuthSession.user_id == user.id,
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > datetime.now(UTC),
        )
        total = await session.scalar(select(func.count()).select_from(AuthSession).where(*criteria))
        rows = (
            await session.scalars(
                select(AuthSession)
                .where(*criteria)
                .order_by(AuthSession.created_at.desc(), AuthSession.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
        items = [
            {
                "id": row.id,
                "created_at": row.created_at.isoformat(),
                "expires_at": row.expires_at.isoformat(),
                "user_agent": row.user_agent,
            }
            for row in rows
        ]
    return {"items": items, "total": total or 0, "page": page, "page_size": page_size}
