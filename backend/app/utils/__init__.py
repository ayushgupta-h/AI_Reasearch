"""Shared, dependency-light utilities (SSE framing and stdout capture)."""

from app.utils.sse import (
    build_event,
    detect_agent,
    sse_format,
    strip_ansi,
    QueueStreamWriter,
)
from app.utils.stdout_proxy import capture_stdout, install_stdout_proxy

__all__ = [
    "QueueStreamWriter",
    "build_event",
    "capture_stdout",
    "detect_agent",
    "install_stdout_proxy",
    "sse_format",
    "strip_ansi",
]
