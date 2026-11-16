"""Human-in-the-loop research plan and report review contracts."""

from __future__ import annotations

from typing import Dict, List, Optional
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from app.schemas.base import DomainSchema, LongText, MediumText, OutputSchema, ShortText, UtcDatetime, utc_now
from app.schemas.enums import (
    ApprovalDecision,
    ReviewAction,
    ReviewCommentKind,
    ReviewStatus,
)


class ResearchPlanReviewRecord(OutputSchema):
    """A proposed research plan and its current human-review state."""

    plan_id: UUID
    session_id: UUID
    title: ShortText
    objective: Optional[LongText] = None
    query: LongText
    token_budget: int = Field(0, ge=0)
    agent_count: int = Field(1, ge=1)
    status: ReviewStatus
    review_note: Optional[LongText] = None
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[UtcDatetime] = None
    details: Dict[str, object] = Field(default_factory=dict)
    created_at: UtcDatetime
    updated_at: UtcDatetime


class PlanReviewDecisionRequest(DomainSchema):
    """Approve, reject, or request changes to a research plan."""

    decision: ApprovalDecision
    comment: Optional[LongText] = None

    @model_validator(mode="after")
    def _validate_comment(self) -> "PlanReviewDecisionRequest":
        if self.decision in (
            ApprovalDecision.CHANGES_REQUESTED,
            ApprovalDecision.REJECTED,
        ) and not (self.comment or "").strip():
            raise ValueError("rejections and change requests require a comment")
        return self


class PlanModificationRequest(DomainSchema):
    """A reviewer's requested modification to a proposed plan.

    The workflow keeps the original proposed plan in its audit history; these
    fields describe the requested replacement rather than rewriting history.
    """

    comment: Optional[LongText] = None
    title: Optional[ShortText] = None
    objective: Optional[LongText] = None
    query: Optional[LongText] = None


class ReviewCommentRequest(DomainSchema):
    """A comment or structured correction request."""

    comment: MediumText
    kind: ReviewCommentKind = ReviewCommentKind.GENERAL
    claim_ids: List[UUID] = Field(default_factory=list, max_length=20)


class ReviewCommentRecord(OutputSchema):
    comment_id: UUID
    review_request_id: UUID
    author_id: Optional[str] = None
    body: MediumText
    kind: ReviewCommentKind
    claim_ids: List[UUID] = Field(default_factory=list)
    is_resolved: bool = False
    created_at: UtcDatetime


class ReportReviewRecord(OutputSchema):
    report_id: UUID
    session_id: UUID
    title: ShortText
    status: ReviewStatus
    critic_score: Optional[int] = Field(None, ge=0, le=10)
    latest_decision: Optional[ApprovalDecision] = None
    latest_comment: Optional[LongText] = None
    reviewer_id: Optional[str] = None
    decided_at: Optional[UtcDatetime] = None
    created_at: UtcDatetime
    updated_at: UtcDatetime


class ReportReviewDecisionRequest(DomainSchema):
    """Approve, reject, or request changes to a generated report."""

    decision: ApprovalDecision
    comment: Optional[LongText] = None

    @model_validator(mode="after")
    def _validate_comment(self) -> "ReportReviewDecisionRequest":
        if self.decision in (
            ApprovalDecision.CHANGES_REQUESTED,
            ApprovalDecision.REJECTED,
        ) and not (self.comment or "").strip():
            raise ValueError("rejections and change requests require a comment")
        return self


class ReportRevisionRequest(DomainSchema):
    """Mark a reviewed report as revised after a new version is produced."""

    comment: Optional[MediumText] = None


class ReviewAuditRecord(OutputSchema):
    """One immutable audit entry from the human-review trail."""

    audit_id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    plan_id: Optional[UUID] = None
    report_id: Optional[UUID] = None
    reviewer_id: str
    action: ReviewAction
    previous_status: Optional[str] = None
    new_status: str
    comment: Optional[LongText] = None
    created_at: UtcDatetime = Field(default_factory=utc_now)


class ReviewHistoryResponse(OutputSchema):
    research_id: UUID
    entries: List[ReviewAuditRecord]
    count: int = Field(ge=0)


class PlanReviewResponse(OutputSchema):
    plan: ResearchPlanReviewRecord
    audit: Optional[ReviewAuditRecord] = None
    workflow_resumed: bool = False


class ReportReviewResponse(OutputSchema):
    report: ReportReviewRecord
    audit: Optional[ReviewAuditRecord] = None
    workflow_resumed: bool = False


__all__ = [
    "PlanModificationRequest",
    "PlanReviewDecisionRequest",
    "PlanReviewResponse",
    "ReportReviewDecisionRequest",
    "ReportReviewRecord",
    "ReportReviewResponse",
    "ReportRevisionRequest",
    "ResearchPlanReviewRecord",
    "ReviewAuditRecord",
    "ReviewCommentRecord",
    "ReviewCommentRequest",
    "ReviewHistoryResponse",
]
