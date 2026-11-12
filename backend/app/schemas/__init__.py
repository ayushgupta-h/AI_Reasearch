"""
app/schemas/__init__.py
=======================
Single source of truth for every Pydantic model used across the backend.

The pipeline models (``QueryPlan``, ``QueryValidation``, ``CriticEvaluation``)
are owned by ``query_intake.py`` / ``agent.py`` — those modules import
LangChain, so they are resolved lazily to keep application startup fast.
"""

from __future__ import annotations

from typing import Any

from app.schemas.agent import AgentExecution
from app.schemas.agent_history import (
    AgentExecutionRecord,
    AgentExecutionsResponse,
    ResearchProgressResponse,
    ResearchTimelineResponse,
    TimelineEventRecord,
)
from app.schemas.base import (
    BaseSchema,
    DomainSchema,
    OutputSchema,
    TimestampedSchema,
    UtcDatetime,
    ensure_utc,
    format_utc,
    utc_now,
)
from app.schemas.citation import Citation
from app.schemas.claim import Claim, Contradiction, Evidence
from app.schemas.common import (
    ApiResponse,
    Page,
    PaginationMeta,
    PaginationParams,
    envelope_payload,
)
from app.schemas.enums import (
    AgentExecutionStatus,
    AgentName,
    ApprovalDecision,
    CitationFormat,
    CitationStatus,
    ClaimStatus,
    DocumentProcessingStatus,
    ClaimType,
    ContradictionSeverity,
    ContradictionStatus,
    ErrorCode,
    EvidenceStance,
    HealthState,
    LLMProvider,
    ProjectStatus,
    ProviderCapability,
    ProviderState,
    ReportFormat,
    ReportStatus,
    ReportVerificationStatus,
    ReportVersionStatus,
    ResearchEventType,
    ResearchPriority,
    ResearchTerminalStatus,
    SessionStatus,
    SourceType,
    TaskStatus,
    TimelineEventType,
    UploadPurpose,
)
from app.schemas.documents import (
    DeletedDocumentResponse,
    DocumentListResponse,
    DocumentRecord,
    DocumentStatusResponse,
)
from app.schemas.envelopes import HealthEnvelope, ProvidersEnvelope, SessionsEnvelope
from app.schemas.errors import ApiError
from app.schemas.health import ComponentCheck, HealthStatus
from app.schemas.project import Project
from app.schemas.provider import ProviderStatus
from app.schemas.report import (
    FileUploadMetadata,
    ReportVersion,
    ResearchApproval,
    ResearchReport,
)
from app.schemas.report_management import (
    DeletedReportResponse,
    ReportExportRequest,
    ReportRecord,
    ReportVersionCreateRequest,
    ReportVersionDetail,
    ReportVersionSummary,
    ReportVersionsResponse,
    ResearchReportsResponse,
)
from app.schemas.research import (
    ResearchEvent,
    ResearchId,
    ResearchRequest,
    ResearchResponse,
)
from app.schemas.requests import (
    ChatRequest,
    CitationStyle,
    ExportFormat,
    ExportRequest,
    ProjectCreateRequest,
    ProjectUpdateRequest,
    ProviderName,
    ResearchCreateRequest,
    ResearchRunRequest,
    ResearchUpdateRequest,
)
from app.schemas.responses import (
    AuthConfigResponse,
    AuthMeResponse,
    ChatResponse,
    DeletedProjectResponse,
    DeletedResearchResponse,
    DeletedSessionResponse,
    ExportResult,
    HealthResponse,
    ProjectRecord,
    ProjectsResponse,
    ProvidersResponse,
    ResearchListResponse,
    ResearchRecord,
    SessionRecord,
    SessionsResponse,
    ShareTokenResponse,
)
from app.schemas.session import ResearchSession
from app.schemas.source import ResearchSource
from app.schemas.task import ResearchTask

_PIPELINE_EXPORTS = ("CriticEvaluation", "QueryPlan", "QueryValidation")

__all__ = [
    # Entities
    "AgentExecution",
    "AgentExecutionRecord",
    "AgentExecutionsResponse",
    "Citation",
    "Claim",
    "ComponentCheck",
    "Contradiction",
    "DeletedDocumentResponse",
    "DocumentListResponse",
    "DocumentRecord",
    "DocumentStatusResponse",
    "Evidence",
    "FileUploadMetadata",
    "HealthStatus",
    "Project",
    "ProviderStatus",
    "ReportVersion",
    "ReportExportRequest",
    "ReportRecord",
    "ReportVersionCreateRequest",
    "ReportVersionDetail",
    "ReportVersionSummary",
    "ReportVersionsResponse",
    "ResearchApproval",
    "ResearchEvent",
    "ResearchReport",
    "ResearchRequest",
    "ResearchProgressResponse",
    "ResearchResponse",
    "ResearchSession",
    "ResearchTimelineResponse",
    "ResearchSource",
    "ResearchTask",
    # API models
    "ApiError",
    "ApiResponse",
    "AuthConfigResponse",
    "AuthMeResponse",
    "ChatRequest",
    "ChatResponse",
    "DeletedProjectResponse",
    "DeletedReportResponse",
    "DeletedResearchResponse",
    "DeletedSessionResponse",
    "ExportRequest",
    "ExportResult",
    "HealthEnvelope",
    "HealthResponse",
    "Page",
    "PaginationMeta",
    "PaginationParams",
    "ProjectCreateRequest",
    "ProjectRecord",
    "ProjectUpdateRequest",
    "ProjectsResponse",
    "ProvidersEnvelope",
    "ProvidersResponse",
    "ResearchCreateRequest",
    "ResearchListResponse",
    "ResearchRecord",
    "ResearchReportsResponse",
    "ResearchRunRequest",
    "ResearchUpdateRequest",
    "SessionRecord",
    "SessionsEnvelope",
    "SessionsResponse",
    "ShareTokenResponse",
    # Base types / helpers
    "BaseSchema",
    "DomainSchema",
    "OutputSchema",
    "ResearchId",
    "TimestampedSchema",
    "UtcDatetime",
    "envelope_payload",
    "ensure_utc",
    "format_utc",
    "utc_now",
    # Enums
    "AgentExecutionStatus",
    "AgentName",
    "ApprovalDecision",
    "CitationFormat",
    "CitationStatus",
    "CitationStyle",
    "ClaimStatus",
    "ClaimType",
    "ContradictionSeverity",
    "ContradictionStatus",
    "DocumentProcessingStatus",
    "ErrorCode",
    "EvidenceStance",
    "ExportFormat",
    "HealthState",
    "LLMProvider",
    "ProjectStatus",
    "ProviderCapability",
    "ProviderName",
    "ProviderState",
    "ReportFormat",
    "ReportStatus",
    "ReportVerificationStatus",
    "ReportVersionStatus",
    "ResearchEventType",
    "ResearchPriority",
    "ResearchTerminalStatus",
    "SessionStatus",
    "SourceType",
    "TaskStatus",
    "TimelineEventRecord",
    "TimelineEventType",
    "UploadPurpose",
    # Lazy pipeline models
    "CriticEvaluation",
    "QueryPlan",
    "QueryValidation",
]


def __getattr__(name: str) -> Any:
    if name in _PIPELINE_EXPORTS:
        from app.schemas import pipeline

        return getattr(pipeline, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(__all__)
