"""
app/schemas/responses.py
========================
Response models for the public HTTP API.

These stay deliberately close to the original payloads: the frontend reads
``sessions``/``count``, ``providers``/``default`` and the flat health keys.
New fields are additive and optional.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.base import OutputSchema, UtcDatetime
from app.schemas.common import PaginationMeta
from app.schemas.enums import LLMProvider, ProjectStatus, ResearchPriority, SessionStatus
from app.schemas.health import HealthStatus
from app.schemas.provider import ProviderStatus

# Backwards-compatible name: ``/health`` now returns the richer HealthStatus.
HealthResponse = HealthStatus


class ProvidersResponse(BaseModel):
    """Supported providers plus optional non-sensitive status details."""

    model_config = ConfigDict(extra="ignore")

    providers: List[str]
    default: str
    statuses: Optional[List[ProviderStatus]] = None


class SessionRecord(BaseModel):
    """A single persisted research session (API view of a SQLite row).

    Kept permissive and string-based for backward compatibility with the
    existing ``research_sessions`` table and the frontend.
    """

    model_config = ConfigDict(extra="ignore")

    session_id: str
    topic: str
    timestamp: str
    provider: Optional[str] = None
    critic_score: Optional[int] = None
    revision_count: Optional[int] = 0
    status: Optional[str] = None
    saved_report_path: Optional[str] = None
    citations_verified: Optional[int] = 0
    citations_total: Optional[int] = 0
    duration_seconds: Optional[float] = None
    user_id: Optional[str] = None


class SessionsResponse(BaseModel):
    sessions: List[SessionRecord]
    count: int
    pagination: Optional[PaginationMeta] = None


class ProjectRecord(OutputSchema):
    """A project owned by the authenticated user."""

    project_id: str
    owner_id: str
    name: str
    description: Optional[str] = None
    research_domain: Optional[str] = None
    status: ProjectStatus
    created_at: UtcDatetime
    updated_at: UtcDatetime


class ProjectsResponse(OutputSchema):
    projects: List[ProjectRecord]
    count: int
    pagination: PaginationMeta


class ResearchRecord(OutputSchema):
    """A research session that belongs to a project."""

    research_id: str
    project_id: str
    owner_id: str
    topic: str
    provider: Optional[LLMProvider] = None
    status: SessionStatus
    priority: ResearchPriority
    notes: Optional[str] = None
    created_at: UtcDatetime
    updated_at: UtcDatetime


class ResearchListResponse(OutputSchema):
    research_sessions: List[ResearchRecord]
    count: int
    pagination: PaginationMeta


class DeletedProjectResponse(OutputSchema):
    status: str
    project_id: str


class DeletedResearchResponse(OutputSchema):
    status: str
    research_id: str


class ChatResponse(BaseModel):
    reply: str
    provider: str


class ShareTokenResponse(BaseModel):
    filename: str
    share_token: str
    share_url: str


class DeletedSessionResponse(BaseModel):
    status: str
    session_id: str


class AuthConfigResponse(BaseModel):
    supabaseUrl: str
    supabaseAnonKey: str


class AuthMeResponse(BaseModel):
    status: str
    user: Dict[str, Any] = Field(default_factory=dict)


class ExportResult(BaseModel):
    """Metadata returned when an export is written (not used by the blob route)."""

    filename: str
    media_type: str
    size_bytes: int = Field(ge=0)
