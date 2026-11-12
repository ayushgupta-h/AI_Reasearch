"""
app/schemas/enums.py
====================
Canonical enums for every status/state value exposed by the API.

All enums inherit ``str`` so they serialize as plain strings, keeping JSON
payloads byte-compatible with existing clients and SQLite text columns.
"""

from __future__ import annotations

from enum import Enum


class LLMProvider(str, Enum):
    GROQ = "groq"
    GEMINI = "gemini"
    MISTRAL = "mistral"
    HUGGINGFACE = "huggingface"


class ResearchPriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class SessionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ResearchTerminalStatus(str, Enum):
    """Statuses a finished research run may report."""

    COMPLETED = "completed"
    CANCELLED = "cancelled"


class ResearchEventType(str, Enum):
    PROGRESS = "progress"
    AGENT_START = "agent_start"
    AGENT_DONE = "agent_done"
    COMPLETE = "complete"
    ERROR = "error"


class TimelineEventType(str, Enum):
    """Lifecycle events recorded for a research run's timeline."""

    RESEARCH_CREATED = "research_created"
    RESEARCH_STARTED = "research_started"
    AGENT_STARTED = "agent_started"
    AGENT_COMPLETED = "agent_completed"
    AGENT_FAILED = "agent_failed"
    RESEARCH_COMPLETED = "research_completed"
    RESEARCH_FAILED = "research_failed"
    RESEARCH_CANCELLED = "research_cancelled"


class TaskStatus(str, Enum):
    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class AgentName(str, Enum):
    STRATEGIST = "strategist"
    SEARCH = "search"
    READER = "reader"
    RAG = "rag"
    ANALYST = "analyst"
    WRITER = "writer"
    CRITIC = "critic"
    REFINER = "refiner"
    EXPORTER = "exporter"
    CITATION_VALIDATOR = "citation_validator"


class AgentExecutionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class ProjectStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class SourceType(str, Enum):
    ACADEMIC = "academic"
    WEB = "web"
    NEWS = "news"
    ENCYCLOPEDIA = "encyclopedia"
    TECHNICAL = "technical"
    OTHER = "other"


class CitationStatus(str, Enum):
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    BROKEN = "broken"
    TIMEOUT = "timeout"
    ERROR = "error"


class CitationFormat(str, Enum):
    APA = "apa"
    MLA = "mla"
    HARVARD = "harvard"


class ClaimType(str, Enum):
    FACTUAL = "factual"
    STATISTICAL = "statistical"
    CAUSAL = "causal"
    COMPARATIVE = "comparative"
    PREDICTIVE = "predictive"
    DEFINITIONAL = "definitional"


class ClaimStatus(str, Enum):
    UNVERIFIED = "unverified"
    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class EvidenceStance(str, Enum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    NEUTRAL = "neutral"
    CONTEXTUAL = "contextual"


class ContradictionSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ContradictionStatus(str, Enum):
    UNRESOLVED = "unresolved"
    RESOLVED = "resolved"
    ACCEPTED_AMBIGUITY = "accepted_ambiguity"


class ReportStatus(str, Enum):
    DRAFT = "draft"
    UNDER_REVIEW = "under_review"
    APPROVED = "approved"
    REVISED = "revised"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class ApprovalDecision(str, Enum):
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"
    REJECTED = "rejected"


class DocumentProcessingStatus(str, Enum):
    """Lifecycle of an uploaded document.

    ``indexed`` is only valid once extraction, chunking and embedding have
    actually succeeded; uploads start at ``uploaded``.
    """

    UPLOADED = "uploaded"
    PENDING = "pending"
    EXTRACTING = "extracting"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    INDEXED = "indexed"
    FAILED = "failed"


class ReportFormat(str, Enum):
    """Formats supported by the existing report exporter."""

    MD = "md"
    DOCX = "docx"
    PDF = "pdf"


class ReportVersionStatus(str, Enum):
    """Lifecycle of a report version."""

    DRAFT = "draft"
    REVIEWING = "reviewing"
    REVISION_REQUESTED = "revision_requested"
    APPROVED = "approved"
    PUBLISHED = "published"


class ReportVerificationStatus(str, Enum):
    """Citation verification state recorded for a report version."""

    UNVERIFIED = "unverified"
    PARTIALLY_VERIFIED = "partially_verified"
    VERIFIED = "verified"
    FAILED = "failed"


class UploadPurpose(str, Enum):
    SOURCE_DOCUMENT = "source_document"
    ATTACHMENT = "attachment"
    REPORT_EXPORT = "report_export"


class ProviderState(str, Enum):
    READY = "ready"
    MISSING_CREDENTIALS = "missing_credentials"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class ProviderCapability(str, Enum):
    CHAT = "chat"
    RESEARCH = "research"
    TOOL_CALLING = "tool_calling"


class HealthState(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"
    ERROR = "error"


class ErrorCode(str, Enum):
    """Mirrors the codes emitted by :mod:`app.errors`."""

    BAD_REQUEST = "bad_request"
    VALIDATION_ERROR = "validation_error"
    AUTHENTICATION_REQUIRED = "authentication_required"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    METHOD_NOT_ALLOWED = "method_not_allowed"
    RATE_LIMITED = "rate_limited"
    DATABASE_UNAVAILABLE = "database_unavailable"
    LLM_PROVIDER_UNAVAILABLE = "llm_provider_unavailable"
    ORCHESTRATION_FAILED = "orchestration_failed"
    RESEARCH_EXECUTION_FAILED = "research_execution_failed"
    AGENT_EXECUTION_FAILED = "agent_execution_failed"
    SEARCH_PROVIDER_UNAVAILABLE = "search_provider_unavailable"
    VECTOR_STORE_FAILED = "vector_store_failed"
    CITATION_VALIDATION_FAILED = "citation_validation_failed"
    REPORT_GENERATION_FAILED = "report_generation_failed"
    RAG_FAILED = "rag_failed"
    FILE_PROCESSING_FAILED = "file_processing_failed"
    STREAM_ERROR = "stream_error"
    CONFIGURATION_ERROR = "configuration_error"
    SERVICE_UNAVAILABLE = "service_unavailable"
    HTTP_ERROR = "http_error"
    INTERNAL_ERROR = "internal_error"
