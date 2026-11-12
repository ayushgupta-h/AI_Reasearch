"""
app/schemas/session.py
======================
Persisted research session contract.

``SessionRecord`` in ``responses.py`` is the backward-compatible API view of a
SQLite row (string IDs and timestamp, permissive). ``ResearchSession`` is the
canonical typed entity used by newer/typed code paths.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas.base import MediumText, OutputSchema, Title, UtcDatetime, utc_now
from app.schemas.enums import LLMProvider, SessionStatus


class ResearchSession(OutputSchema):
    """A research run persisted in the session store."""

    session_id: UUID
    topic: Title
    status: SessionStatus = SessionStatus.PENDING
    provider: Optional[LLMProvider] = None
    critic_score: Optional[int] = Field(None, ge=0, le=10)
    revision_count: int = Field(0, ge=0)
    saved_report_path: Optional[str] = Field(None, max_length=4096)
    duration_seconds: Optional[float] = Field(None, ge=0, le=86_400)
    citations_verified: int = Field(0, ge=0)
    citations_total: int = Field(0, ge=0)
    user_id: Optional[UUID] = None
    notes: Optional[MediumText] = None
    created_at: UtcDatetime = Field(default_factory=utc_now)
    updated_at: Optional[UtcDatetime] = None

    @model_validator(mode="after")
    def _validate_session_consistency(self) -> "ResearchSession":
        if self.citations_verified > self.citations_total:
            raise ValueError("citations_verified cannot exceed citations_total")
        if self.updated_at is not None and self.updated_at < self.created_at:
            raise ValueError("updated_at must be greater than or equal to created_at")
        return self
