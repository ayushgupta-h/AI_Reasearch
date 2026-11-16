"""Authenticated human plan/report review endpoints."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Path

from app.errors import AuthenticationError, ValidationError
from app.schemas.enums import ApprovalDecision
from app.schemas.errors import ApiError
from app.schemas.review import (
    PlanModificationRequest,
    PlanReviewDecisionRequest,
    PlanReviewResponse,
    ReportReviewDecisionRequest,
    ReportReviewRecord,
    ReportReviewResponse,
    ReportRevisionRequest,
    ResearchPlanReviewRecord,
    ReviewAuditRecord,
    ReviewCommentRecord,
    ReviewCommentRequest,
    ReviewHistoryResponse,
)
from app.security.dependencies import get_current_user
from app.services.container import get_review_service
from app.services.review_service import ResearchReviewService

router = APIRouter(tags=["Human Review"])

# Existing research/report ids are uuid4().hex (32 chars); PostgreSQL UUIDs
# may include dashes. Accept both while keeping malformed paths out.
_ID_PATTERN = r"^[a-f0-9-]{32,36}$"
ResearchIdPath = Path(..., pattern=_ID_PATTERN, description="Research session id.")
ReportIdPath = Path(..., pattern=_ID_PATTERN, description="Report id.")

_ERRORS = {
    400: {"model": ApiError, "description": "Invalid review action."},
    401: {"model": ApiError, "description": "Authentication required."},
    404: {"model": ApiError, "description": "Research plan or report not found."},
    409: {"model": ApiError, "description": "Review already has a final decision."},
    422: {"model": ApiError, "description": "Invalid review payload or id."},
    503: {"model": ApiError, "description": "Review storage unavailable."},
}


def _owner(current_user: dict) -> str:
    owner = str(current_user.get("user_id") or "").strip()
    if not owner:
        raise AuthenticationError("Authentication required.")
    return owner


# ── Research plan review ────────────────────────────────────────────────────


@router.get(
    "/research/{research_id}/plan",
    response_model=ResearchPlanReviewRecord,
    responses=_ERRORS,
    summary="Get the proposed research plan for review",
)
async def get_research_plan(
    research_id: str = ResearchIdPath,
    current_user: dict = Depends(get_current_user),
    service: ResearchReviewService = Depends(get_review_service),
) -> ResearchPlanReviewRecord:
    return service.get_plan(research_id, _owner(current_user))


@router.post(
    "/research/{research_id}/plan/decision",
    response_model=PlanReviewResponse,
    responses=_ERRORS,
    summary="Approve, reject, or request changes to a research plan",
)
async def decide_research_plan(
    payload: PlanReviewDecisionRequest,
    research_id: str = ResearchIdPath,
    current_user: dict = Depends(get_current_user),
    service: ResearchReviewService = Depends(get_review_service),
) -> PlanReviewResponse:
    return service.decide_plan(
        session_id=research_id,
        owner_id=_owner(current_user),
        decision=payload.decision,
        comment=payload.comment,
    )


@router.post(
    "/research/{research_id}/plan/approve",
    response_model=PlanReviewResponse,
    responses=_ERRORS,
    summary="Approve a proposed research plan",
)
async def approve_research_plan(
    research_id: str = ResearchIdPath,
    payload: Optional[PlanModificationRequest] = None,
    current_user: dict = Depends(get_current_user),
    service: ResearchReviewService = Depends(get_review_service),
) -> PlanReviewResponse:
    return service.decide_plan(
        session_id=research_id,
        owner_id=_owner(current_user),
        decision=ApprovalDecision.APPROVED,
        comment=payload.comment if payload else None,
    )


@router.post(
    "/research/{research_id}/plan/reject",
    response_model=PlanReviewResponse,
    responses=_ERRORS,
    summary="Reject a proposed research plan",
)
async def reject_research_plan(
    research_id: str = ResearchIdPath,
    payload: Optional[PlanModificationRequest] = None,
    current_user: dict = Depends(get_current_user),
    service: ResearchReviewService = Depends(get_review_service),
) -> PlanReviewResponse:
    comment = payload.comment if payload else None
    if not (comment or "").strip():
        raise ValidationError("A comment is required to reject a plan.")
    return service.decide_plan(
        session_id=research_id,
        owner_id=_owner(current_user),
        decision=ApprovalDecision.REJECTED,
        comment=comment,
    )


@router.post(
    "/research/{research_id}/plan/request-changes",
    response_model=PlanReviewResponse,
    responses=_ERRORS,
    summary="Request modifications to a proposed research plan",
)
async def request_research_plan_changes(
    payload: PlanModificationRequest,
    research_id: str = ResearchIdPath,
    current_user: dict = Depends(get_current_user),
    service: ResearchReviewService = Depends(get_review_service),
) -> PlanReviewResponse:
    if not (payload.comment or "").strip():
        raise ValidationError("A comment is required when requesting plan changes.")
    return service.decide_plan(
        session_id=research_id,
        owner_id=_owner(current_user),
        decision=ApprovalDecision.CHANGES_REQUESTED,
        comment=payload.comment,
    )


@router.post(
    "/research/{research_id}/plan/comments",
    response_model=ReviewAuditRecord,
    responses=_ERRORS,
    summary="Add a comment to a proposed research plan",
)
async def add_research_plan_comment(
    payload: ReviewCommentRequest,
    research_id: str = ResearchIdPath,
    current_user: dict = Depends(get_current_user),
    service: ResearchReviewService = Depends(get_review_service),
) -> ReviewAuditRecord:
    return service.add_plan_comment(
        session_id=research_id,
        owner_id=_owner(current_user),
        comment=payload.comment,
    )


@router.get(
    "/research/{research_id}/reviews",
    response_model=ReviewHistoryResponse,
    responses=_ERRORS,
    summary="List the audit history for a research review",
)
async def get_research_review_history(
    research_id: str = ResearchIdPath,
    current_user: dict = Depends(get_current_user),
    service: ResearchReviewService = Depends(get_review_service),
) -> ReviewHistoryResponse:
    return service.history(research_id, _owner(current_user))


# ── Report review ───────────────────────────────────────────────────────────


@router.get(
    "/reports/{report_id}/reviews",
    response_model=ReportReviewRecord,
    responses=_ERRORS,
    summary="Get a report's current review state",
)
async def get_report_review(
    report_id: str = ReportIdPath,
    current_user: dict = Depends(get_current_user),
    service: ResearchReviewService = Depends(get_review_service),
) -> ReportReviewRecord:
    return service.get_report(report_id, _owner(current_user))


@router.post(
    "/reports/{report_id}/reviews/submit",
    response_model=ReportReviewResponse,
    responses=_ERRORS,
    summary="Submit a generated report for human review",
)
async def submit_report_for_review(
    report_id: str = ReportIdPath,
    current_user: dict = Depends(get_current_user),
    service: ResearchReviewService = Depends(get_review_service),
) -> ReportReviewResponse:
    return service.submit_report(report_id, _owner(current_user))


@router.post(
    "/reports/{report_id}/reviews",
    response_model=ReportReviewResponse,
    responses=_ERRORS,
    summary="Submit a report review decision",
)
async def submit_report_review(
    payload: ReportReviewDecisionRequest,
    report_id: str = ReportIdPath,
    current_user: dict = Depends(get_current_user),
    service: ResearchReviewService = Depends(get_review_service),
) -> ReportReviewResponse:
    return service.decide_report(
        report_id=report_id,
        owner_id=_owner(current_user),
        decision=payload.decision,
        comment=payload.comment,
    )


@router.get(
    "/reports/{report_id}/reviews/history",
    response_model=ReviewHistoryResponse,
    responses=_ERRORS,
    summary="List the audit history for a report's research session",
)
async def get_report_review_history(
    report_id: str = ReportIdPath,
    current_user: dict = Depends(get_current_user),
    service: ResearchReviewService = Depends(get_review_service),
) -> ReviewHistoryResponse:
    return service.report_history(report_id, _owner(current_user))


@router.post(
    "/reports/{report_id}/reviews/decision",
    response_model=ReportReviewResponse,
    responses=_ERRORS,
    summary="Approve, reject, or request changes to a report",
)
async def decide_report_review(
    payload: ReportReviewDecisionRequest,
    report_id: str = ReportIdPath,
    current_user: dict = Depends(get_current_user),
    service: ResearchReviewService = Depends(get_review_service),
) -> ReportReviewResponse:
    return service.decide_report(
        report_id=report_id,
        owner_id=_owner(current_user),
        decision=payload.decision,
        comment=payload.comment,
    )


@router.post(
    "/reports/{report_id}/reviews/comments",
    response_model=ReviewCommentRecord,
    responses=_ERRORS,
    summary="Add a correction, unsupported-claim note, or research request",
)
async def add_report_review_comment(
    payload: ReviewCommentRequest,
    report_id: str = ReportIdPath,
    current_user: dict = Depends(get_current_user),
    service: ResearchReviewService = Depends(get_review_service),
) -> ReviewCommentRecord:
    return service.add_report_comment(
        report_id=report_id,
        owner_id=_owner(current_user),
        comment=payload.comment,
        kind=payload.kind,
        claim_ids=[str(item) for item in payload.claim_ids],
    )


@router.post(
    "/reports/{report_id}/reviews/revise",
    response_model=ReportReviewResponse,
    responses=_ERRORS,
    summary="Mark a report as revised after requested changes",
)
async def mark_report_revised(
    report_id: str = ReportIdPath,
    payload: Optional[ReportRevisionRequest] = None,
    current_user: dict = Depends(get_current_user),
    service: ResearchReviewService = Depends(get_review_service),
) -> ReportReviewResponse:
    return service.mark_report_revised(
        report_id=report_id,
        owner_id=_owner(current_user),
        comment=payload.comment if payload else None,
    )
