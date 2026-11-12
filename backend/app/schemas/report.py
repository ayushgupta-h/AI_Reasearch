"""Report, report-version, approval and file-upload metadata schemas."""

from __future__ import annotations

from typing import List, Optional
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from app.schemas.base import (
    FileName,
    MarkdownContent,
    MediumText,
    MimeType,
    OutputSchema,
    Sha256Hex,
    Title,
    UtcDatetime,
    utc_now,
)
from app.schemas.enums import ApprovalDecision, ReportStatus, UploadPurpose


class ResearchReport(OutputSchema):
    """A generated research report and its aggregate quality metrics."""

    report_id: UUID = Field(default_factory=uuid4)
    session_id: Optional[UUID] = None
    project_id: Optional[UUID] = None
    title: Title
    status: ReportStatus = ReportStatus.DRAFT
    current_version: int = Field(1, ge=1, le=1_000)
    critic_score: Optional[int] = Field(None, ge=0, le=10)
    word_count: Optional[int] = Field(None, ge=0, le=2_000_000)
    citations_verified: int = Field(0, ge=0)
    citations_total: int = Field(0, ge=0)
    created_at: UtcDatetime = Field(default_factory=utc_now)
    updated_at: Optional[UtcDatetime] = None
    approved_at: Optional[UtcDatetime] = None

    @model_validator(mode="after")
    def _validate_report_state(self) -> "ResearchReport":
        if self.citations_verified > self.citations_total:
            raise ValueError("citations_verified cannot exceed citations_total")
        if self.updated_at is not None and self.updated_at < self.created_at:
            raise ValueError("updated_at must be greater than or equal to created_at")
        if self.status in (ReportStatus.APPROVED, ReportStatus.PUBLISHED) and self.approved_at is None:
            raise ValueError("approved/published reports must include approved_at")
        if self.approved_at is not None and self.approved_at < self.created_at:
            raise ValueError("approved_at cannot be earlier than created_at")
        return self


class ReportVersion(OutputSchema):
    """An immutable snapshot of a report at a specific revision."""

    version_id: UUID = Field(default_factory=uuid4)
    report_id: UUID
    version: int = Field(..., ge=1, le=1_000)
    content_markdown: MarkdownContent
    critic_score: Optional[int] = Field(None, ge=0, le=10)
    change_summary: Optional[MediumText] = None
    created_at: UtcDatetime = Field(default_factory=utc_now)
    created_by: Optional[UUID] = None
    is_current: bool = False


class ResearchApproval(OutputSchema):
    """A review decision recorded against a report."""

    approval_id: UUID = Field(default_factory=uuid4)
    report_id: UUID
    session_id: Optional[UUID] = None
    decision: ApprovalDecision
    reviewer_id: Optional[UUID] = None
    comment: Optional[MediumText] = None
    decided_at: UtcDatetime = Field(default_factory=utc_now)
    critic_score_at_approval: Optional[int] = Field(None, ge=0, le=10)

    @model_validator(mode="after")
    def _validate_decision(self) -> "ResearchApproval":
        if (
            self.decision in (ApprovalDecision.CHANGES_REQUESTED, ApprovalDecision.REJECTED)
            and not self.comment
        ):
            raise ValueError("rejections and change requests require a comment")
        if self.decided_at > utc_now():
            raise ValueError("decided_at cannot be in the future")
        return self


class FileUploadMetadata(OutputSchema):
    """Metadata for a user-uploaded research document.

    Only metadata is modelled here; no storage backend is implied or required.
    """

    file_id: UUID = Field(default_factory=uuid4)
    filename: FileName
    content_type: MimeType
    size_bytes: int = Field(..., ge=0, le=26_214_400)  # 25 MiB
    checksum_sha256: Optional[Sha256Hex] = None
    purpose: UploadPurpose = UploadPurpose.ATTACHMENT
    uploaded_at: UtcDatetime = Field(default_factory=utc_now)
    uploaded_by: Optional[UUID] = None
    session_id: Optional[UUID] = None
    tags: List[str] = Field(default_factory=list, max_length=10)


__all__ = [
    "ResearchReport",
    "ReportVersion",
    "ResearchApproval",
    "FileUploadMetadata",
]
