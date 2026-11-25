from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.security import decode_access_token
from app.infrastructure.database import SessionLocal
from app.infrastructure.models import AuthSession, User
from app.modules.auth import application
from app.modules.auth.schemas import LoginRequest, RegisterRequest

router = APIRouter(prefix="/api/v1/auth", tags=["Authentication"])
bearer = HTTPBearer(auto_error=False)
REFRESH_COOKIE = "ledgerflow_refresh"
COOKIE_PATH = "/api/v1/auth"


def _same_origin(request: Request) -> None:
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise AppError("CSRF_REJECTED", "Cross-site authentication request rejected.", 403)
    origin = request.headers.get("origin")
    allowed = {str(request.base_url).rstrip("/")}
    if get_settings().sandbox_mode:
        allowed.update(
            {
                "http://localhost:5173",
                "http://127.0.0.1:5173",
                "http://localhost:8000",
                "http://127.0.0.1:8000",
            }
        )
    if origin and origin not in allowed:
        raise AppError("CSRF_REJECTED", "Authentication origin is not allowed.", 403)


def _set_refresh(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        REFRESH_COOKIE,
        token,
        max_age=settings.refresh_token_days * 24 * 60 * 60,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path=COOKIE_PATH,
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


async def current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AppError("UNAUTHENTICATED", "A valid access token is required.", 401)
    claims = decode_access_token(credentials.credentials)
    async with SessionLocal() as session:
        row = await session.get(AuthSession, claims["sid"])
        user = await session.get(User, claims["sub"])
        if (
            row is None
            or user is None
            or row.user_id != user.id
            or row.revoked_at is not None
            or row.expires_at <= datetime.now(UTC)
            or not user.active
            or (user.is_sandbox and not get_settings().sandbox_mode)
        ):
            raise AppError("UNAUTHENTICATED", "Session is expired or revoked.", 401)
    request.state.auth_session_id = row.id
    return user


Actor = Annotated[User, Depends(current_user)]


@router.post("/register", status_code=201)
async def register(body: RegisterRequest, request: Request) -> dict[str, Any]:
    _same_origin(request)
    return await application.register(body.email, body.password, body.display_name)


@router.post("/login")
async def login(body: LoginRequest, request: Request, response: Response) -> dict[str, Any]:
    _same_origin(request)
    client_ip = request.client.host if request.client else "unknown"
    result = await application.login(
        body.email, body.password, client_ip, request.headers.get("user-agent", "")
    )
    _set_refresh(response, result.refresh_token)
    return result.response()


@router.post("/refresh")
async def refresh(request: Request, response: Response) -> dict[str, Any]:
    _same_origin(request)
    result = await application.refresh(
        request.cookies.get(REFRESH_COOKIE, ""), request.headers.get("user-agent", "")
    )
    _set_refresh(response, result.refresh_token)
    return result.response()


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> dict[str, str]:
    _same_origin(request)
    await application.revoke_by_token(request.cookies.get(REFRESH_COOKIE))
    if credentials is not None:
        try:
            actor = await current_user(request, credentials)
        except AppError:
            # Logout is idempotent even for an already expired/revoked token.
            pass
        else:
            await application.revoke_session(actor, request.state.auth_session_id)
    response.delete_cookie(
        REFRESH_COOKIE,
        path=COOKIE_PATH,
        httponly=True,
        secure=get_settings().cookie_secure,
        samesite="strict",
    )
    response.headers["Cache-Control"] = "no-store"
    return {"status": "logged_out"}


@router.get("/me")
async def me(actor: Actor) -> dict[str, Any]:
    return application.public_user(actor)


@router.get("/sessions")
async def sessions(
    actor: Actor,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    return await application.list_sessions(actor, page, page_size)


@router.delete("/sessions/{session_id}")
async def revoke_session(session_id: UUID, actor: Actor) -> dict[str, str]:
    await application.revoke_session(actor, str(session_id))
    return {"status": "revoked"}
