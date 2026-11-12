"""
app/observability.py
====================
Structured logging with correlation IDs, secret redaction and JSON output.

Every log record carries (when available):
  * ``request_id``  — one per inbound HTTP request (also returned as
    ``X-Request-ID`` and in error payloads).
  * ``research_id`` — one per research pipeline execution.
  * ``agent`` / ``stage`` — the LangGraph node currently executing.
  * ``duration_ms`` — attached to completion events.

Redaction runs on every record: bearer tokens, JWTs, provider API keys and
``?token=`` query parameters never reach a log sink.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Iterator, Optional

# ─── Correlation context ─────────────────────────────────────────────────────

_REQUEST_ID: ContextVar[str] = ContextVar("request_id", default="-")
_RESEARCH_ID: ContextVar[str] = ContextVar("research_id", default="-")
_USER_ID: ContextVar[str] = ContextVar("user_id", default="-")
_AGENT: ContextVar[str] = ContextVar("agent", default="-")
_STAGE: ContextVar[str] = ContextVar("stage", default="-")
_TRACE_ID: ContextVar[str] = ContextVar("trace_id", default="-")
_SPAN_ID: ContextVar[str] = ContextVar("span_id", default="-")


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def new_research_id() -> str:
    return uuid.uuid4().hex


def current_request_id() -> str:
    return _REQUEST_ID.get()


def current_research_id() -> str:
    return _RESEARCH_ID.get()


def current_user_id() -> str:
    return _USER_ID.get()


def current_trace_id() -> str:
    return _TRACE_ID.get()


def current_span_id() -> str:
    return _SPAN_ID.get()


def bind_trace_context(trace_id: Optional[str] = None, span_id: Optional[str] = None) -> None:
    """Bind distributed tracing IDs to the log context."""
    if trace_id:
        _TRACE_ID.set(str(trace_id))
    if span_id:
        _SPAN_ID.set(str(span_id))


def reset_trace_context() -> None:
    """Reset trace context bindings."""
    _TRACE_ID.set("-")
    _SPAN_ID.set("-")


def bind_user(user_id: Any) -> None:
    """Bind the authenticated user to the log context for this request.

    Never pass an email, JWT or raw token here — log ``user_id`` only.
    """
    _USER_ID.set(str(user_id) if user_id else "-")


def reset_user_context() -> None:
    """Clear the user binding so it cannot leak into another request."""
    _USER_ID.set("-")


@contextmanager
def log_context(**values: Any) -> Iterator[None]:
    """Temporarily bind correlation fields for the current context.

    Supported keys: ``request_id``, ``research_id``, ``agent``, ``stage``.
    Unknown keys raise ``ValueError`` so typos fail loudly in tests.
    """
    setters = {
        "request_id": _REQUEST_ID,
        "research_id": _RESEARCH_ID,
        "user_id": _USER_ID,
        "agent": _AGENT,
        "stage": _STAGE,
        "trace_id": _TRACE_ID,
        "span_id": _SPAN_ID,
    }
    tokens = []
    try:
        for key, value in values.items():
            var = setters.get(key)
            if var is None:
                raise ValueError(f"Unsupported log context key: {key!r}")
            tokens.append((var, var.set(str(value))))
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)


# ─── Redaction ───────────────────────────────────────────────────────────────

_REDACTED = "***"

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # JWT (three dot-separated base64url segments)
    (
        re.compile(r"eyJ[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}"),
        _REDACTED,
    ),
    # Bearer headers
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{8,}"), r"\1" + _REDACTED),
    # Known provider key shapes
    (
        re.compile(
            r"(?i)\b(sk-[A-Za-z0-9_\-]{12,}|sb_publishable_[A-Za-z0-9_\-]{6,}"
            r"|AIza[0-9A-Za-z_\-]{20,}|gsk_[A-Za-z0-9]{10,}|hf_[A-Za-z0-9]{10,}"
            r"|tvly-[A-Za-z0-9_\-]{10,})"
        ),
        _REDACTED,
    ),
    # Credentials in query strings (?token=…, &share=…)
    (
        re.compile(r"(?i)([?&](?:token|share|apikey|api_key|access_token)=)[^&\s\"']+"),
        r"\1" + _REDACTED,
    ),
    # Secrets written as key=value / key: value (passwords, keys, tokens)
    (
        re.compile(
            r"(?i)\b(password|passwd|pwd|secret|client_secret|api_key|apikey"
            r"|access_token|refresh_token|encryption_key|private_key)"
            r"\s*[=:]\s*([^\s,;&\"']+)"
        ),
        r"\1=" + _REDACTED,
    ),
)


def redact(value: Any) -> str:
    """Return ``value`` as text with credentials masked."""
    text = value if isinstance(value, str) else str(value)
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


# ─── Formatters ──────────────────────────────────────────────────────────────


class _ContextFilter(logging.Filter):
    """Copy contextvars onto the record for formatters."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _REQUEST_ID.get()
        record.research_id = _RESEARCH_ID.get()
        record.user_id = _USER_ID.get()
        record.agent = _AGENT.get()
        record.agent_name = _AGENT.get()
        record.stage = _STAGE.get()
        record.trace_id = _TRACE_ID.get()
        record.span_id = _SPAN_ID.get()
        if not hasattr(record, "event"):
            record.event = None
        if not hasattr(record, "duration_ms"):
            record.duration_ms = None
        if not hasattr(record, "error_code"):
            record.error_code = None
        if not hasattr(record, "status_code"):
            record.status_code = None
        # Redact the fully formatted message exactly once.
        try:
            record.msg = redact(record.getMessage())
            record.args = ()
        except Exception:  # pragma: no cover - defensive
            record.msg = redact(str(record.msg))
            record.args = ()
        return True


def _event_name(message: str) -> str:
    """Derive a stable event key (``request.completed``) from a log message."""
    token = message.strip().split(" ", 1)[0]
    return token or "log"


class ConsoleFormatter(logging.Formatter):
    """Human-readable single-line format for local development."""

    default_time_format = "%Y-%m-%dT%H:%M:%S"

    def __init__(self) -> None:
        super().__init__(
            fmt="%(asctime)s.%(msecs)03d %(levelname)-7s "
            "req=%(request_id)s run=%(research_id)s "
            "%(name)s: %(message)s",
            datefmt=self.default_time_format,
        )

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extras = []
        if getattr(record, "agent", "-") not in ("-", None):
            extras.append(f"agent={record.agent}")
        if getattr(record, "trace_id", "-") not in ("-", None):
            extras.append(f"trace={record.trace_id}")
        if getattr(record, "duration_ms", None) is not None:
            extras.append(f"duration_ms={record.duration_ms}")
        if getattr(record, "error_code", None):
            extras.append(f"error_code={record.error_code}")
        if extras:
            base = f"{base} [{' '.join(extras)}]"
        return base


class JsonFormatter(logging.Formatter):
    """One JSON object per line — suitable for log aggregators."""

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        payload: dict[str, Any] = {
            "timestamp": time.strftime(
                "%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)
            )
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", None) or _event_name(message),
            "message": message,
            "request_id": getattr(record, "request_id", "-"),
        }
        for field_name in ("user_id", "research_id", "trace_id", "span_id"):
            value = getattr(record, field_name, None)
            if value not in (None, "-", ""):
                payload[field_name] = value
        agent_name = getattr(record, "agent_name", None) or getattr(record, "agent", None)
        if agent_name not in (None, "-", ""):
            payload["agent_name"] = agent_name
            payload["agent"] = agent_name
        for optional in (
            "stage",
            "duration_ms",
            "error_code",
            "status_code",
            "path",
            "method",
            "provider",
            "report_filename",
            "read_only",
        ):
            value = getattr(record, optional, None)
            if value not in (None, "-", ""):
                payload[optional] = value
        if record.exc_info:
            payload["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, default=str)


# ─── Configuration ───────────────────────────────────────────────────────────

_HANDLER_FLAG = "_rai_handler"


def configure_logging(
    level: str = "INFO",
    fmt: str = "console",
    log_file: Optional[Path] = None,
    stream: Any = None,
) -> None:
    """Install application logging handlers (idempotent)."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, _HANDLER_FLAG, False):
            root.removeHandler(handler)
            handler.close()

    formatter: logging.Formatter = JsonFormatter() if fmt == "json" else ConsoleFormatter()
    context_filter = _ContextFilter()

    handlers: list[logging.Handler] = [
        logging.StreamHandler(stream or sys.stderr),
    ]
    if log_file is not None:
        try:
            Path(log_file).parent.mkdir(parents=True, exist_ok=True)
            handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
        except OSError:  # pragma: no cover - unwritable path should not stop the app
            logging.getLogger("app.observability").warning(
                "log_file_unavailable path=%s", log_file
            )

    for handler in handlers:
        handler.setFormatter(formatter)
        handler.addFilter(context_filter)
        setattr(handler, _HANDLER_FLAG, True)
        root.addHandler(handler)

    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Keep third-party HTTP noise out of request logs.
    for noisy in ("httpx", "httpcore", "urllib3", "chromadb"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
