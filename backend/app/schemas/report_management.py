"""Report metadata, immutable versions and export request/response schemas."""

from __future__ import annotations

from typing import Annotated, List, Optional

from pydantic import Field, StringConstraints

from app.schemas.base import DomainSchema, MarkdownContent, MediumText, OutputSchema, Title, UtcDatetime
from app.schemas.common import PaginationMeta
from app.schemas.enums import (
    CitationFormat,
    ReportFormat,
    ReportVerificationStatus,
    ReportVersionStatus,
)

ReportId = Annotated[
    str, StringConstraints(pattern=r"^[a-f0-9]{32}$", min_length=32, max_length=32)
]
VersionId = Annotated[
    str, StringConstraints(pattern=r"^[a-f0-9]{32}$", min_length=32, max_length=32)
]


class ReportRecord(OutputSchema):
    """A report and its current version metadata."""

    report_id: ReportId
    research_id: ReportId
    owner_id: str
    title: Title
    format: ReportFormat
    version: int = Field(..., ge=1, le=1_000)
    status: ReportVersionStatus
    critic_score: Optional[int] = Field(None, ge=0, le=10)
    verification_status: ReportVerificationStatus
    storage_reference: Optional[str] = Field(None, max_length=512)
    created_at: UtcDatetime
    updated_at: UtcDatetime


class ResearchReportsResponse(OutputSchema):
    research_id: ReportId
    reports: List[ReportRecord]
    count: int = Field(ge=0)
    pagination: PaginationMeta


class ReportVersionSummary(OutputSchema):
    """Version metadata without the report body."""

    version_id: VersionId
    report_id: ReportId
    version: int = Field(..., ge=1, le=1_000)
    status: ReportVersionStatus
    critic_score: Optional[int] = Field(None, ge=0, le=10)
    verification_status: ReportVerificationStatus
    storage_reference: Optional[str] = Field(None, max_length=512)
    change_summary: Optional[MediumText] = None
    created_at: UtcDatetime


class ReportVersionDetail(ReportVersionSummary):
    content_markdown: MarkdownContent


class ReportVersionsResponse(OutputSchema):
    report_id: ReportId
    versions: List[ReportVersionSummary]
    count: int = Field(ge=0)


class ReportVersionCreateRequest(DomainSchema):
    """A new immutable version; existing versions are never mutated."""

    content_markdown: MarkdownContent
    status: ReportVersionStatus = ReportVersionStatus.DRAFT
    critic_score: Optional[int] = Field(None, ge=0, le=10)
    verification_status: ReportVerificationStatus = ReportVerificationStatus.UNVERIFIED
    change_summary: Optional[MediumText] = None


class ReportExportRequest(DomainSchema):
    format: ReportFormat = ReportFormat.PDF
    version_id: Optional[VersionId] = None
    citation_style: CitationFormat = CitationFormat.APA


class DeletedReportResponse(OutputSchema):
    status: str
    report_id: ReportId


__all__ = [
    "DeletedReportResponse",
    "ReportExportRequest",
    "ReportId",
    "ReportRecord",
    "ReportVersionCreateRequest",
    "ReportVersionDetail",
    "ReportVersionSummary",
    "ReportVersionsResponse",
    "ResearchReportsResponse",
    "VersionId",
]
