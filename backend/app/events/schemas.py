"""
Canonical distributed research event schema (TASK 2.5).

The event envelope is intentionally transport-neutral. Redis Streams stores the
flat serialized form returned by :meth:`ResearchStreamEvent.redis_fields`;
PostgreSQL stores the same fields in ``research_event_log``. The API converts
the envelope to the legacy SSE frame shape so existing clients keep working.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Mapping

from pydantic import Field, field_validator

from app.schemas.base import DomainSchema, UtcDatetime, ensure_utc, format_utc, utc_now


class ResearchStreamEventType(str, Enum):
    """Every event type that can travel over the distributed stream."""

    RESEARCH_QUEUED = "research.queued"
    RESEARCH_STARTED = "research.started"
    AGENT_STARTED = "agent.started"
    AGENT_PROGRESS = "agent.progress"
    AGENT_COMPLETED = "agent.completed"
    AGENT_FAILED = "agent.failed"
    SOURCE_DISCOVERED = "source.discovered"
    CLAIM_EXTRACTED = "claim.extracted"
    CLAIM_VERIFIED = "claim.verified"
    CONTRADICTION_DETECTED = "contradiction.detected"
    REPORT_GENERATED = "report.generated"
    REVIEW_REQUIRED = "review.required"
    RESEARCH_COMPLETED = "research.completed"
    RESEARCH_FAILED = "research.failed"


TERMINAL_EVENT_TYPES = frozenset(
    {
        ResearchStreamEventType.RESEARCH_COMPLETED,
        ResearchStreamEventType.RESEARCH_FAILED,
    }
)


def _anonymous_user() -> str:
    return "anonymous"


class ResearchStreamEvent(DomainSchema):
    """Immutable event persisted in Redis Streams and PostgreSQL.

    ``sequence_number`` is assigned by the Redis-side publisher under a
    per-research lock, so ordering is stable even with concurrent Celery
    workers. ``0`` is reserved for a best-effort PostgreSQL journal entry made
    when Redis is unavailable; streamed events always start at 1.
    """

    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex, min_length=1, max_length=64)
    research_id: str = Field(..., min_length=1, max_length=64)
    user_id: str = Field(default_factory=_anonymous_user, min_length=1, max_length=128)
    event_type: ResearchStreamEventType
    timestamp: UtcDatetime = Field(default_factory=utc_now)
    payload: Dict[str, Any] = Field(default_factory=dict)
    sequence_number: int = Field(..., ge=0)

    @field_validator("user_id")
    @classmethod
    def _normalise_user(cls, value: str) -> str:
        value = value.strip()
        return value or _anonymous_user()

    def redis_fields(self) -> Dict[str, str]:
        """Flat field map written with ``XADD`` and read back with ``XRANGE``."""
        return {
            "event_id": self.event_id,
            "research_id": self.research_id,
            "user_id": self.user_id,
            "event_type": self.event_type.value,
            "timestamp": format_utc(self.timestamp),
            "payload": json.dumps(self.payload, ensure_ascii=False, default=str),
            "sequence_number": str(self.sequence_number),
        }

    @classmethod
    def from_redis(cls, fields: Mapping[str, Any]) -> "ResearchStreamEvent":
        """Parse one Redis Stream field map (``bytes`` or ``str`` values)."""

        def _text(key: str) -> str:
            value = fields.get(key, "")
            if isinstance(value, bytes):
                value = value.decode("utf-8")
            return str(value)

        payload_raw = _text("payload") or "{}"
        try:
            payload = json.loads(payload_raw)
        except (TypeError, ValueError):
            payload = {"raw": payload_raw}
        if not isinstance(payload, dict):
            payload = {"value": payload}

        timestamp_raw = _text("timestamp")
        try:
            timestamp = datetime.fromisoformat(timestamp_raw.replace("Z", "+00:00"))
        except ValueError:
            timestamp = utc_now()

        return cls(
            event_id=_text("event_id") or uuid.uuid4().hex,
            research_id=_text("research_id"),
            user_id=_text("user_id") or _anonymous_user(),
            event_type=ResearchStreamEventType(_text("event_type")),
            timestamp=ensure_utc(timestamp),
            payload=payload,
            sequence_number=int(_text("sequence_number") or 0),
        )


__all__ = [
    "ResearchStreamEvent",
    "ResearchStreamEventType",
    "TERMINAL_EVENT_TYPES",
]
