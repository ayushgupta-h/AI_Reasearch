"""
app/schemas/registry.py
=======================
Registry of every schema that belongs to the public contract.

The registry powers OpenAPI generation: all models listed here are injected into
``components.schemas`` even before an endpoint consumes them, so the generated
documentation describes the full data model of the system.
"""

from __future__ import annotations

from typing import Dict, Tuple, Type

from pydantic import BaseModel

from app.schemas.agent import AgentExecution
from app.schemas.agent_history import (
    AgentExecutionRecord,
    AgentExecutionsResponse,
    ResearchProgressResponse,
    ResearchTimelineResponse,
    TimelineEventRecord,
)
from app.schemas.citation import Citation
from app.schemas.claim import Claim, Contradiction, Evidence
from app.schemas.common import PaginationMeta, PaginationParams
from app.schemas.documents import (
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
    ReportRecord,
    ReportVersionDetail,
    ReportVersionSummary,
    ReportVersionsResponse,
    ResearchReportsResponse,
)
from app.events.schemas import ResearchStreamEvent
from app.schemas.research import ResearchEvent, ResearchRequest, ResearchResponse
from app.schemas.requests import ChatRequest, ExportRequest, ResearchRunRequest
from app.schemas.responses import (
    AuthConfigResponse,
    AuthMeResponse,
    ChatResponse,
    DeletedSessionResponse,
    ProvidersResponse,
    SessionRecord,
    SessionsResponse,
    ShareTokenResponse,
)
from app.schemas.session import ResearchSession
from app.schemas.source import ResearchSource
from app.schemas.task import ResearchTask

# The 20 contract families required by stage 1.4.
REQUIRED_CONTRACT_SCHEMAS: Tuple[str, ...] = (
    "ResearchRequest",
    "ResearchResponse",
    "ResearchSession",
    "Project",
    "ResearchTask",
    "AgentExecution",
    "ResearchSource",
    "Citation",
    "Claim",
    "Evidence",
    "Contradiction",
    "ResearchReport",
    "ReportVersion",
    "ChatRequest",
    "ChatResponse",
    "FileUploadMetadata",
    "ResearchApproval",
    "ApiError",
    "ProviderStatus",
    "HealthStatus",
)

CONTRACT_SCHEMAS: Dict[str, Type[BaseModel]] = {
    # Required contract families
    "ResearchRequest": ResearchRequest,
    "ResearchResponse": ResearchResponse,
    "ResearchSession": ResearchSession,
    "Project": Project,
    "ResearchTask": ResearchTask,
    "AgentExecution": AgentExecution,
    "ResearchSource": ResearchSource,
    "Citation": Citation,
    "Claim": Claim,
    "Evidence": Evidence,
    "Contradiction": Contradiction,
    "ResearchReport": ResearchReport,
    "ReportVersion": ReportVersion,
    "ChatRequest": ChatRequest,
    "ChatResponse": ChatResponse,
    "FileUploadMetadata": FileUploadMetadata,
    "ResearchApproval": ResearchApproval,
    "ApiError": ApiError,
    "ProviderStatus": ProviderStatus,
    "HealthStatus": HealthStatus,
    # Supporting contracts referenced by the API / docs
    "ResearchEvent": ResearchEvent,
    "ResearchStreamEvent": ResearchStreamEvent,
    "ResearchRunRequest": ResearchRunRequest,
    "ExportRequest": ExportRequest,
    "PaginationParams": PaginationParams,
    "PaginationMeta": PaginationMeta,
    "ComponentCheck": ComponentCheck,
    "SessionRecord": SessionRecord,
    "SessionsResponse": SessionsResponse,
    "ProvidersResponse": ProvidersResponse,
    "ShareTokenResponse": ShareTokenResponse,
    "DeletedSessionResponse": DeletedSessionResponse,
    "AuthConfigResponse": AuthConfigResponse,
    "AuthMeResponse": AuthMeResponse,
    "HealthEnvelope": HealthEnvelope,
    "ProvidersEnvelope": ProvidersEnvelope,
    "SessionsEnvelope": SessionsEnvelope,
    # Agent execution history
    "AgentExecutionRecord": AgentExecutionRecord,
    "AgentExecutionsResponse": AgentExecutionsResponse,
    "TimelineEventRecord": TimelineEventRecord,
    "ResearchTimelineResponse": ResearchTimelineResponse,
    "ResearchProgressResponse": ResearchProgressResponse,
    # Documents
    "DocumentRecord": DocumentRecord,
    "DocumentListResponse": DocumentListResponse,
    "DocumentStatusResponse": DocumentStatusResponse,
    # Report management
    "ReportRecord": ReportRecord,
    "ResearchReportsResponse": ResearchReportsResponse,
    "ReportVersionSummary": ReportVersionSummary,
    "ReportVersionDetail": ReportVersionDetail,
    "ReportVersionsResponse": ReportVersionsResponse,
}
