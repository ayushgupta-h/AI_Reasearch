"""
app/utils/sse.py
================
Server-Sent Event formatting helpers and agent label detection.

The event names (``progress``, ``agent_start``, ``complete``, ``error``) and
payload shape are part of the frontend contract and are unchanged.
"""

from __future__ import annotations

import json
import queue
import re
from typing import Optional

ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|\x1b\][^\x07]*\x07")
RICH_TAG = re.compile(r"\[/?[a-zA-Z0-9 _#]*\]")

# Agent name -> friendly label, matched against orchestrator log lines.
AGENT_LABELS: tuple[tuple[str, str], ...] = (
    ("AGENT 1", "Strategist"),
    ("AGENT 2", "Search Engine"),
    ("AGENT 3", "Deep Reader"),
    ("AGENT 4", "Analyst"),
    ("AGENT 5", "Report Writer"),
    ("AGENT 6", "Critic"),
    ("AGENT 7", "Refiner"),
    ("AGENT 8", "Exporter"),
    ("CITATION VALIDATOR", "Citation Validator"),
    ("MULTI-FORMAT EXPORT", "Multi-Format Export"),
)


def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes and Rich markup from terminal output."""
    text = ANSI_ESCAPE.sub("", text)
    text = RICH_TAG.sub("", text)
    return text.strip()


def detect_agent(message: str) -> Optional[str]:
    """Return the agent label announced by *message*, if any."""
    upper = message.upper()
    for key, label in AGENT_LABELS:
        if key in upper:
            return label
    return None


def sse_format(event_type: str, payload: dict) -> str:
    """Serialise *payload* as a valid SSE event."""
    data = json.dumps(payload, ensure_ascii=False)
    return f"event: {event_type}\ndata: {data}\n\n"


def build_event(
    event: str,
    message: str,
    agent: Optional[str] = None,
    data: Optional[dict] = None,
) -> str:
    """Build the SSE frame consumed by the frontend."""
    payload: dict = {"event": event, "message": message}
    if agent:
        payload["agent"] = agent
    if data:
        payload["data"] = data
    return sse_format(event, payload)


class QueueStreamWriter:
    """File-like object turning orchestrator stdout lines into SSE events.

    Each research execution gets its own writer, attached to the calling thread
    through :mod:`app.utils.stdout_proxy`, so concurrent runs never mix output.
    """

    def __init__(self, event_queue: "queue.Queue", passthrough=None) -> None:
        self._queue = event_queue
        self._passthrough = passthrough
        self._buffer = ""

    def write(self, text: str) -> int:
        if self._passthrough is not None:
            try:
                self._passthrough.write(text)
            except Exception:  # pragma: no cover - console is best effort
                pass
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            clean = strip_ansi(line.strip())
            if clean:
                self._queue.put(("progress", clean))
        return len(text)

    def flush(self) -> None:
        if self._passthrough is not None:
            try:
                self._passthrough.flush()
            except Exception:  # pragma: no cover
                pass

    def reconfigure(self, **_kwargs) -> None:
        """No-op shim: Rich may try to reconfigure the captured stream."""

    @property
    def encoding(self) -> str:
        return "utf-8"
