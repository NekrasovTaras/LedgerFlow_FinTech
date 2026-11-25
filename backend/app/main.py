import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.body_limit import RequestBodyLimit
from app.core.config import get_settings
from app.core.context import correlation_id
from app.core.errors import AppError
from app.core.observability import (
    HTTP_DURATION,
    HTTP_REQUESTS,
    configure_logging,
    configure_tracing,
)
from app.infrastructure.database import SessionLocal, engine


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    from app.bootstrap import validate_runtime_mode

    await validate_runtime_mode()
    yield
    await engine.dispose()


app = FastAPI(
    title="LedgerFlow",
    description="Financial Infrastructure & Payment Processing Engine. Virtual funds only.",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(RequestBodyLimit)
configure_logging()
configure_tracing(app)


@app.middleware("http")
async def request_context(request: Request, call_next: Any) -> Response:
    started = time.monotonic()
    status_code = 500
    identifier = request.headers.get("X-Correlation-ID", "")
    if (
        not identifier
        or len(identifier) > 80
        or not all(char.isalnum() or char in "-_." for char in identifier)
    ):
        identifier = str(uuid4())
    token = correlation_id.set(identifier)
    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers["X-Correlation-ID"] = identifier
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response
    finally:
        duration = time.monotonic() - started
        HTTP_REQUESTS.labels(method=request.method, status=str(status_code)).inc()
        HTTP_DURATION.observe(duration)
        logging.getLogger("ledgerflow.http").info(
            "request completed: method=%s status=%s duration_ms=%s",
            request.method,
            status_code,
            round(duration * 1000),
        )
        correlation_id.reset(token)


@app.exception_handler(AppError)
async def application_error(request: Request, error: AppError) -> JSONResponse:
    return JSONResponse(
        {**error.payload(), "correlation_id": correlation_id.get()}, status_code=error.status
    )


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
    # Pydantic errors can contain the submitted password/token: never echo input.
    details = [
        {"loc": item["loc"], "type": item["type"], "message": item["msg"]}
        for item in error.errors()
    ]
    return JSONResponse(
        {
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Request validation failed.",
                "details": details,
            },
            "correlation_id": correlation_id.get(),
        },
        422,
    )


@app.exception_handler(DBAPIError)
async def database_error(request: Request, error: DBAPIError) -> JSONResponse:
    code = getattr(error.orig, "sqlstate", None)
    status = 409 if code in {"23505", "23514", "23503", "40P01", "40001"} else 503
    return JSONResponse(
        {
            "error": {
                "code": "DATABASE_CONFLICT" if status == 409 else "DATABASE_UNAVAILABLE",
                "message": (
                    "Operation could not be committed safely; retry with the same idempotency key."
                ),
            },
            "correlation_id": correlation_id.get(),
        },
        status,
    )


@app.get("/health/live", tags=["Health"])
async def live() -> dict[str, str]:
    return {"status": "ok", "service": "ledgerflow", "version": "0.1.0"}


@app.get("/health/ready", tags=["Health"])
async def ready() -> dict[str, Any]:
    async with SessionLocal() as session:
        await session.execute(text("SELECT 1 FROM alembic_version LIMIT 1"))
    return {"status": "ok", "postgres": "ready", "sandbox_mode": get_settings().sandbox_mode}


@app.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    return Response(generate_latest(), headers={"Content-Type": CONTENT_TYPE_LATEST})


from app.api import router as api_router  # noqa: E402
from app.modules.auth import router as auth_router  # noqa: E402
from app.modules.operations.presentation import router as operations_router  # noqa: E402

app.include_router(auth_router)
app.include_router(api_router)
app.include_router(operations_router)
