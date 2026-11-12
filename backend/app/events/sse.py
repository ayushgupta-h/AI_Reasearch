"""Legacy-compatible SSE rendering for canonical research stream events.

The new distributed endpoint emits canonical event types, but the existing
frontend already understands ``progress``, ``agent_start``, ``agent_done``,
``complete`` and ``error``. Each frame therefore keeps that legacy shape and
adds the canonical fields in ``data`` (and sends the sequence number as the SSE
``id`` for Last-Event-ID reconnects).
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from app.events.schemas import ResearchStreamEvent, ResearchStreamEventType
from app.observability import redact
from app.schemas.base import format_utc

_LEGACY_EVENT = {
    ResearchStreamEventType.RESEARCH_QUEUED: "progress",
    ResearchStreamEventType.RESEARCH_STARTED: "progress",
    ResearchStreamEventType.AGENT_STARTED: "agent_start",
    ResearchStreamEventType.AGENT_PROGRESS: "progress",
    ResearchStreamEventType.AGENT_COMPLETED: "agent_done",
    ResearchStreamEventType.AGENT_FAILED: "error",
    ResearchStreamEventType.SOURCE_DISCOVERED: "progress",
    ResearchStreamEventType.CLAIM_EXTRACTED: "progress",
    ResearchStreamEventType.CLAIM_VERIFIED: "progress",
    ResearchStreamEventType.CONTRADICTION_DETECTED: "progress",
    ResearchStreamEventType.REPORT_GENERATED: "progress",
    ResearchStreamEventType.REVIEW_REQUIRED: "progress",
    ResearchStreamEventType.RESEARCH_COMPLETED: "complete",
    ResearchStreamEventType.RESEARCH_FAILED: "error",
}

_TERMINAL = {
    ResearchStreamEventType.RESEARCH_COMPLETED,
    ResearchStreamEventType.RESEARCH_FAILED,
}


def _agent_name(event: ResearchStreamEvent) -> Optional[str]:
    for key in ("agent", "agent_name"):
        value = event.payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:64]
    return None


def _message(event: ResearchStreamEvent) -> str:
    explicit = event.payload.get("message")
    if isinstance(explicit, str) and explicit.strip():
        return redact(explicit.strip())[:2_000]

    agent = _agent_name(event) or "agent"
    event_type = event.event_type
    if event_type == ResearchStreamEventType.RESEARCH_QUEUED:
        return "Research queued."
    if event_type == ResearchStreamEventType.RESEARCH_STARTED:
        return "Research execution started."
    if event_type == ResearchStreamEventType.AGENT_STARTED:
        return f"Agent active: {agent}"
    if event_type == ResearchStreamEventType.AGENT_PROGRESS:
        return event.payload.get("progress", "Agent progress.")[:2_000]
    if event_type == ResearchStreamEventType.AGENT_COMPLETED:
        return f"Agent completed: {agent}"
    if event_type == ResearchStreamEventType.AGENT_FAILED:
        return f"Agent failed: {agent}"
    if event_type == ResearchStreamEventType.SOURCE_DISCOVERED:
        return f"Discovered {event.payload.get('count', 0)} sources."
    if event_type == ResearchStreamEventType.CLAIM_EXTRACTED:
        return "Claims extracted from the evidence."
    if event_type == ResearchStreamEventType.CLAIM_VERIFIED:
        return "Claim verification completed."
    if event_type == ResearchStreamEventType.CONTRADICTION_DETECTED:
        return "A contradiction was detected."
    if event_type == ResearchStreamEventType.REPORT_GENERATED:
        return "Research report generated."
    if event_type == ResearchStreamEventType.REVIEW_REQUIRED:
        return "Human review is required."
    if event_type == ResearchStreamEventType.RESEARCH_COMPLETED:
        return "Research report successfully generated."
    return "Research execution failed."


def event_to_sse(event: ResearchStreamEvent) -> str:
    """Render one canonical event as a backward-compatible SSE frame."""
    legacy = _LEGACY_EVENT[event.event_type]
    agent = _agent_name(event)
    data: Dict[str, Any] = dict(event.payload)
    data.update(
        {
            "event_id": event.event_id,
            "research_id": event.research_id,
            "event_type": event.event_type.value,
            "timestamp": format_utc(event.timestamp),
            "sequence_number": event.sequence_number,
        }
    )
    # The envelope's owner is deliberately not sent to the browser.
    data.pop("user_id", None)

    body: Dict[str, Any] = {
        "event": legacy,
        "message": _message(event),
        "data": data,
    }
    if agent:
        body["agent"] = agent
    serialized = json.dumps(body, ensure_ascii=False, default=str)
    return f"id: {event.sequence_number}\nevent: {legacy}\ndata: {serialized}\n\n"


def terminal_event(event: ResearchStreamEvent) -> bool:
    return event.event_type in _TERMINAL


__all__ = ["event_to_sse", "terminal_event"]
