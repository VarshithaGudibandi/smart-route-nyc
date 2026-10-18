"""ASGI middleware: request IDs, access logging, security headers and size limits.

Written as plain ASGI middleware rather than ``BaseHTTPMiddleware`` so that
context variables set here are visible to the endpoint and error handlers.
"""

import contextlib
import time

import structlog
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import error_response
from app.core.metrics import HTTP_DURATION, HTTP_REQUESTS
from app.core.request_context import REQUEST_ID_HEADER, resolve_request_id, set_request_id

logger = structlog.get_logger("app.access")


class RequestContextMiddleware:
    """Assigns a request ID, binds it to logs and echoes it in the response."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = resolve_request_id(Headers(scope=scope).get(REQUEST_ID_HEADER))
        set_request_id(request_id)
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        started = time.perf_counter()
        status_code = 500

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message).append(REQUEST_ID_HEADER, request_id)
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            elapsed = time.perf_counter() - started
            # Path only: query strings can contain coordinates.
            logger.info(
                "request",
                method=scope["method"],
                path=scope["path"],
                status=status_code,
                duration_ms=round(elapsed * 1000, 1),
            )
            route = _route_template(scope)
            HTTP_REQUESTS.labels(scope["method"], route, str(status_code)).inc()
            HTTP_DURATION.labels(scope["method"], route).observe(elapsed)


def _route_template(scope: Scope) -> str:
    """The matched route's path template, so IDs never become metric labels."""
    route = scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else "unmatched"


# The API returns only JSON, so nothing it sends should ever run as a page.
API_SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
}
# The interactive docs load Swagger UI from its CDN; they keep the rest.
DOCS_PATHS = ("/api/docs", "/api/openapi.json")


class SecurityHeadersMiddleware:
    """Adds defensive headers to every response, plus HSTS when enabled."""

    def __init__(self, app: ASGIApp, hsts: bool = False) -> None:
        self.app = app
        self.hsts = hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        is_docs = scope["path"].startswith(DOCS_PATHS)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in API_SECURITY_HEADERS.items():
                    if is_docs and name == "Content-Security-Policy":
                        continue
                    headers.setdefault(name, value)
                if self.hsts:
                    headers.setdefault("Strict-Transport-Security", "max-age=31536000")
            await send(message)

        await self.app(scope, receive, send_with_headers)


class RequestSizeLimitMiddleware:
    """Rejects request bodies larger than ``max_bytes`` with 413.

    Checks ``Content-Length`` up front and also counts streamed bytes, so a
    chunked request without the header cannot bypass the limit.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = Headers(scope=scope).get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.max_bytes:
            await self._reject(scope, receive, send)
            return

        received = 0
        too_large = False

        async def limited_receive() -> Message:
            nonlocal received, too_large
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    too_large = True
                    raise _BodyTooLargeError
            return message

        async def guarded_send(message: Message) -> None:
            # Frameworks may turn the read error into their own response
            # (FastAPI answers 400); discard it so the client gets a 413.
            if not too_large:
                await send(message)

        with contextlib.suppress(_BodyTooLargeError):
            await self.app(scope, limited_receive, guarded_send)
        if too_large:
            await self._reject(scope, receive, send)

    async def _reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        response = error_response(413, "payload_too_large", "Request body is too large")
        await response(scope, receive, send)


class _BodyTooLargeError(Exception):
    pass
