"""
app/telemetry/middleware.py
===========================
Pure-ASGI HTTP Telemetry and Tracing Middleware (TASK 2.6).

Extracts W3C traceparent headers, records API metrics (request count,
latencies, error rates, concurrent connections) and coordinates correlation IDs.
"""

from __future__ import annotations

import time
from typing import Any, Awaitable, Callable, MutableMapping

from opentelemetry import trace

from app.observability import (
    bind_trace_context,
    get_logger,
    log_context,
    new_request_id,
    reset_trace_context,
    reset_user_context,
)
from app.telemetry.metrics import get_metrics
from app.telemetry.tracing import extract_trace_context, get_tracer

logger = get_logger("app.telemetry.http")

ASGIApp = Callable[
    [MutableMapping[str, Any], Callable[[], Awaitable[dict]], Callable[[dict], Awaitable[None]]],
    Awaitable[None],
]


class HttpTelemetryMiddleware:
    """Measure HTTP request performance and manage distributed trace contexts."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._metrics = get_metrics()
        self._tracer = get_tracer()

    async def __call__(self, scope: MutableMapping[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "GET")
        path = scope.get("path", "/")

        # 1. Track concurrent connections
        try:
            self._metrics.http_concurrent_connections.inc()
        except Exception:
            pass

        # 2. Extract incoming W3C headers
        headers_raw = dict(scope.get("headers") or [])
        header_map = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in headers_raw.items()
        }
        parent_context = extract_trace_context(header_map)

        request_id = header_map.get("x-request-id") or new_request_id()
        scope.setdefault("state", {})["request_id"] = request_id
        reset_user_context()

        started = time.perf_counter()
        status_code = 500

        # Start root / server span
        span = self._tracer.start_span(
            f"HTTP {method} {path}",
            context=parent_context,
            kind=trace.SpanKind.SERVER,
            attributes={
                "http.method": method,
                "http.target": path,
                "http.route": path,
                "request_id": request_id,
            },
        )

        trace_id_str = "-"
        span_id_str = "-"
        span_ctx = span.get_span_context()
        if span_ctx.is_valid:
            trace_id_str = format(span_ctx.trace_id, "032x")
            span_id_str = format(span_ctx.span_id, "016x")
            bind_trace_context(trace_id=trace_id_str, span_id=span_id_str)

        async def send_wrapper(message: dict) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message.get("status", 500))
                resp_headers = list(message.get("headers") or [])
                resp_headers.append((b"x-request-id", request_id.encode("latin-1")))
                if trace_id_str != "-":
                    resp_headers.append((b"x-trace-id", trace_id_str.encode("latin-1")))
                message["headers"] = resp_headers
            await send(message)

        with log_context(request_id=request_id, trace_id=trace_id_str, span_id=span_id_str):
            try:
                await self.app(scope, receive, send_wrapper)
            except Exception as exc:
                span.record_exception(exc)
                span.set_status(trace.Status(trace.StatusCode.ERROR, str(exc)[:200]))
                raise
            finally:
                duration = time.perf_counter() - started
                try:
                    span.set_attribute("http.status_code", status_code)
                    if status_code >= 500:
                        span.set_status(trace.Status(trace.StatusCode.ERROR))
                    elif status_code < 400:
                        span.set_status(trace.Status(trace.StatusCode.OK))
                except Exception:
                    pass
                span.end()

                # Decrement concurrent connections
                try:
                    self._metrics.http_concurrent_connections.dec()
                except Exception:
                    pass

                # Record Prometheus metrics
                self._metrics.record_http_request(
                    method=method,
                    path=path,
                    status_code=status_code,
                    duration_seconds=duration,
                )

                reset_trace_context()
                reset_user_context()

