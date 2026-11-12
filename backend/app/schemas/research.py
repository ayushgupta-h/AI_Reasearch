"""
app/schemas/research.py
=======================
Research request/response contracts and the SSE event schema.

``ResearchRunRequest`` (``requests.py``) remains the HTTP body for
``POST /research`` and is unchanged for compatibility. ``ResearchRequest`` here
is the canonical, stricter domain request used when a run is persisted or
scheduled. ``ResearchResponse`` describes the ``complete`` SSE ``data`` payload.
"""

from __future__ import annotations

from typing import Annotated, Any, Dict, List, Optional
from uuid import UUID

from pydantic import Field, StringConstraints, field_validator

from app.schemas.base import DomainSchema, Name, OutputSchema, Title
from app.schemas.enums import (
    LLMProvider,
    ResearchEventType,
    ResearchPriority,
    ResearchTerminalStatus,
    SourceType,
)

ResearchId = Annotated[
    str,
    StringConstraints(pattern=r"^[a-f0-9]{32}$", min_length=32, max_length=32),
]
SubQuery = Annotated[str, StringConstraints(min_length=3, max_length=300, strip_whitespace=True)]
MetadataValue = Annotated[str, StringConstraints(max_length=200, strip_whitespace=True)]


class ResearchRequest(DomainSchema):
    """Canonical research submission (domain-level, stricter than the HTTP body)."""

    topic: Title = Field(..., description="Research topic or question to investigate.")
    provider: LLMProvider = Field(
        LLMProvider.GROQ, description="Preferred LLM provider for the run."
    )
    research_type: Optional[Name] = Field(
        None, description="Plan classification produced by the intake agent."
    )
    source_types: List[SourceType] = Field(
        default_factory=lambda: [SourceType.ACADEMIC, SourceType.WEB, SourceType.NEWS],
        min_length=1,
        max_length=6,
    )
    max_revisions: int = Field(2, ge=0, le=5)
    approval_threshold: int = Field(8, ge=1, le=10)
    priority: ResearchPriority = ResearchPriority.NORMAL
    language: Annotated[
        str, StringConstraints(pattern=r"^[a-z]{2}(?:-[A-Z]{2})?$")
    ] = "en"
    project_id: Optional[UUID] = None
    metadata: Dict[
        Annotated[str, StringConstraints(min_length=1, max_length=40)], MetadataValue
    ] = Field(default_factory=dict, max_length=20)

    @field_validator("source_types")
    @classmethod
    def _deduplicate_sources(cls, value: List[SourceType]) -> List[SourceType]:
        deduplicated: List[SourceType] = []
        for item in value:
            if item not in deduplicated:
                deduplicated.append(item)
        if not deduplicated:
            raise ValueError("at least one source type is required")
        return deduplicated


class ResearchResponse(OutputSchema):
    """Payload of the SSE ``complete`` event for a research run."""

    status: ResearchTerminalStatus
    provider: LLMProvider
    critic_score: Optional[int] = Field(None, ge=0, le=10)
    revision_count: int = Field(0, ge=0)
    saved_report_path: Optional[str] = Field(None, max_length=4096)
    sources_analyzed: int = Field(0, ge=0)
    logs: List[Annotated[str, StringConstraints(max_length=2_000)]] = Field(
        default_factory=list, max_length=500
    )
    research_id: Optional[ResearchId] = None
    session_id: Optional[UUID] = None
    report_id: Optional[UUID] = None
    sub_queries: List[SubQuery] = Field(default_factory=list, max_length=12)


class ResearchEvent(OutputSchema):
    """One Server-Sent Event frame.

    ``data`` is typed loosely on purpose: depends on ``event`` (progress events
    carry topic/research_id, completion events match ``ResearchResponse``), and
    the SSE contract must not be forced into an envelope.
    """

    event: ResearchEventType
    message: str = Field(..., min_length=1, max_length=2_000)
    agent: Optional[str] = Field(None, min_length=1, max_length=64)
    data: Optional[Dict[str, Any]] = None
