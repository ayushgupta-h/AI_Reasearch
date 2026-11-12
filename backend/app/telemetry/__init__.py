"""
app/telemetry
=============
Production observability package: Prometheus metrics & OpenTelemetry tracing.
"""

from app.telemetry.callbacks import TracedLLMCallbackHandler
from app.telemetry.metrics import MetricsRegistry, get_metrics
from app.telemetry.middleware import HttpTelemetryMiddleware
from app.telemetry.tracing import (
    extract_trace_context,
    get_tracer,
    inject_trace_context,
    sanitize_span_attributes,
    setup_tracing,
    trace_span,
)

__all__ = [
    "HttpTelemetryMiddleware",
    "MetricsRegistry",
    "TracedLLMCallbackHandler",
    "extract_trace_context",
    "get_metrics",
    "get_tracer",
    "inject_trace_context",
    "sanitize_span_attributes",
    "setup_tracing",
    "trace_span",
]
