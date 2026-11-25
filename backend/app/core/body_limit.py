"""Bound request bodies even when Content-Length is absent or dishonest."""

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.context import correlation_id


class RequestBodyLimit:
    def __init__(self, app: ASGIApp, max_bytes: int = 1_048_576) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        declared = headers.get(b"content-length")
        try:
            length = int(declared) if declared is not None else 0
            if length < 0:
                raise ValueError("Negative Content-Length")
        except ValueError:
            await self.reject(scope, receive, send, 400, "INVALID_CONTENT_LENGTH")
            return
        if length > self.max_bytes:
            await self.reject(scope, receive, send, 413, "REQUEST_TOO_LARGE")
            return
        chunks: list[bytes] = []
        total = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            total += len(chunk)
            if total > self.max_bytes:
                await self.reject(scope, receive, send, 413, "REQUEST_TOO_LARGE")
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)

    async def reject(
        self, scope: Scope, receive: Receive, send: Send, status: int, code: str
    ) -> None:
        response = JSONResponse(
            {
                "error": {
                    "code": code,
                    "message": "Request body rejected by the 1 MiB input limit.",
                },
                "correlation_id": correlation_id.get(),
            },
            status_code=status,
        )
        await response(scope, receive, send)
