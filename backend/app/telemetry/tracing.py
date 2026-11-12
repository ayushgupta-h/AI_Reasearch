"""
app/telemetry/tracing.py
========================
OpenTelemetry distributed tracing integration (TASK 2.6).

Propagates W3C trace context across:
FastAPI -> Celery -> LangGraph -> Agent -> LLM provider -> Search provider.

Correlates research_id and trace_id in logs and spans while redacting
sensitive credentials, API keys, raw documents, and prompts.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
from typing import Any, Dict, Iterator, Mapping, Optional

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from app.observability import bind_trace_context, current_research_id, redact

logger = logging.getLogger("app.telemetry.tracing")

_PROPAGATOR = TraceContextTextMapPropagator()
_TRACER_NAME = "research_ai"
_INITIALIZED = False


def setup_tracing(service_name: str = "research-ai") -> trace.Tracer:
    """Initialize OpenTelemetry TracerProvider once.

    Configures OTLP exporter if OTEL_EXPORTER_OTLP_ENDPOINT is configured in env,
    otherwise uses local tracer without network export.
    """
    global _INITIALIZED
    if _INITIALIZED:
        return trace.get_tracer(_TRACER_NAME)

    try:
        resource = Resource.create({
            "service.name": service_name,
            "service.version": os.getenv("API_VERSION", "2.0.0"),
            "deployment.environment": os.getenv("APP_ENV", "development"),
        })
        provider = TracerProvider(resource=resource)

        otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
        if otlp_endpoint:
            protocol = os.getenv("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf").lower()
            try:
                if "grpc" in protocol:
                    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                        OTLPSpanExporter,
                    )
                    exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
                else:
                    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                        OTLPSpanExporter,
                    )
                    exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
                provider.add_span_processor(BatchSpanProcessor(exporter))
                logger.info("OpenTelemetry OTLP exporter configured: %s", otlp_endpoint)
            except Exception as exc:
                logger.warning("Failed to configure OTLP exporter: %s", exc)

        trace.set_tracer_provider(provider)
        _INITIALIZED = True
    except Exception as exc:
        logger.warning("TracerProvider initialization warning: %s", exc)

    return trace.get_tracer(_TRACER_NAME)


def get_tracer() -> trace.Tracer:
    """Return the global application Tracer."""
    return trace.get_tracer(_TRACER_NAME)


# ── Context Propagation Helpers ─────────────────────────────────────────────


def inject_trace_context(carrier: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Inject current W3C trace context into carrier dict (headers / Celery kwargs)."""
    target = carrier if carrier is not None else {}
    try:
        _PROPAGATOR.inject(target)
    except Exception as exc:
        logger.debug("Failed to inject trace context: %s", exc)
    return target


def extract_trace_context(carrier: Mapping[str, Any]) -> Any:
    """Extract W3C trace context from carrier mapping (headers / Celery kwargs)."""
    try:
        # Standardize keys to lowercase string for header lookup
        normalized = {str(k).lower(): str(v) for k, v in carrier.items() if v is not None}
        return _PROPAGATOR.extract(normalized)
    except Exception as exc:
        logger.debug("Failed to extract trace context: %s", exc)
        return None


# ── Safe Attribute Sanitization ──────────────────────────────────────────────

_SENSITIVE_KEY_PATTERN = re.compile(
    r"(?i)(key|token|secret|password|auth|credential|prompt|body|document)"
)


def sanitize_span_attributes(attributes: Mapping[str, Any]) -> Dict[str, Any]:
    """Filter out or redact sensitive data before setting span attributes."""
    cleaned: Dict[str, Any] = {}
    for k, v in attributes.items():
        key_str = str(k)
        if _SENSITIVE_KEY_PATTERN.search(key_str):
            # Drop or redact
            if any(term in key_str.lower() for term in ("key", "secret", "token", "password", "auth")):
                continue  # Drop keys/tokens entirely
            # For prompt/body/document, summarize length instead of storing raw text
            cleaned[f"{key_str}.length"] = len(str(v)) if v else 0
            continue
        if isinstance(v, (str, int, float, bool)):
            cleaned[key_str] = redact(v) if isinstance(v, str) else v
        else:
            cleaned[key_str] = str(v)[:100]
    return cleaned


# ── Span Context Manager ────────────────────────────────────────────────────


@contextlib.contextmanager
def trace_span(
    name: str,
    attributes: Optional[Dict[str, Any]] = None,
    research_id: Optional[str] = None,
    context: Any = None,
) -> Iterator[trace.Span]:
    """Context manager to create a child span with correlated research_id and logging.

    Failsafe: research execution proceeds even if span creation throws.
    """
    tracer = get_tracer()
    attrs = dict(attributes or {})
    res_id = research_id or current_research_id()
    if res_id and res_id != "-":
        attrs["research.id"] = res_id

    safe_attrs = sanitize_span_attributes(attrs)

    try:
        span_cm = tracer.start_as_current_span(name, attributes=safe_attrs, context=context)
    except Exception:
        # Fallback to no-op
        span_cm = None

    if span_cm is None:
        yield trace.NonRecordingSpan(trace.INVALID_SPAN_CONTEXT)
        return

    with span_cm as span:
        try:
            span_ctx = span.get_span_context()
            if span_ctx.is_valid:
                trace_id_hex = format(span_ctx.trace_id, "032x")
                span_id_hex = format(span_ctx.span_id, "016x")
                bind_trace_context(trace_id=trace_id_hex, span_id=span_id_hex)
        except Exception:
            pass

        try:
            yield span
        except Exception as exc:
            try:
                span.record_exception(exc)
                span.set_status(trace.Status(trace.StatusCode.ERROR, str(exc)[:200]))
            except Exception:
                pass
            raise

