"""
app/middleware.py
=================
Pure-ASGI request context middleware.

Implemented as raw ASGI (not ``BaseHTTPMiddleware``) so Server-Sent Event
responses keep streaming chunk-by-chunk instead of being buffered by an
intermediate task group.

Responsibilities
----------------
* Assign a request ID, expose it as ``request.state.request_id`` and the
  ``X-Request-ID`` response header, and bind it to the logging context.
* Log one ``request.completed`` record with method, path, status and duration.
* Never log query strings (they can carry ``?token=<JWT>``).
"""

from __future__ import annotations

import time
from typing import Any, Awaitable, Callable, MutableMapping

from app.observability import (
    get_logger,
    log_context,
    new_request_id,
    reset_user_context,
)

logger = get_logger("app.request")

ASGIApp = Callable[
    [MutableMapping[str, Any], Callable[[], Awaitable[dict]], Callable[[dict], Awaitable[None]]],
    Awaitable[None],
]


class RequestContextMiddleware:
    """Bind a request ID and emit a completion log for each HTTP request."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:  # type: ignore[no-untyped-def]
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        request_id = new_request_id()
        scope.setdefault("state", {})["request_id"] = request_id
        # Never inherit a previous request's user binding.
        reset_user_context()
        method = scope.get("method", "GET")
        path = scope.get("path", "/")
        started = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: dict) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message.get("status", 500))
                headers = list(message.get("headers") or [])
                headers.append((b"x-request-id", request_id.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        with log_context(request_id=request_id):
            try:
                await self.app(scope, receive, send_wrapper)
            except Exception:
                logger.exception(
                    "request.failed",
                    extra={
                        "path": path,
                        "status_code": 500,
                        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                    },
                )
                raise
            finally:
                duration_ms = round((time.perf_counter() - started) * 1000, 1)
                logger.info(
                    "request.completed",
                    extra={
                        "path": path,
                        "status_code": status_code,
                        "duration_ms": duration_ms,
                        "method": method,
                    },
                )
                reset_user_context()
