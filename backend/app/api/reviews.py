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
from app.security.dependencies import get_current_user, get_optional_user
from app.services.container import get_review_service, get_session_service
from app.services.review_service import ResearchReviewService
from app.services.session_service import SessionService

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


# ── Pending review queues ──────────────────────────────────────────────────


@router.get(
    "/reviews/pending",
    summary="List all pending human review items",
)
@router.get(
    "/reports/pending-reviews",
    summary="List all pending human review items (alias)",
)
async def list_pending_reviews(
    current_user: Optional[dict] = Depends(get_optional_user),
    service: ResearchReviewService = Depends(get_review_service),
    session_service: SessionService = Depends(get_session_service),
) -> dict:
    """Return all pending research plans and reports requiring human decision."""
    user_id = current_user.get("user_id") if current_user else None
    items = []

    # 1. Fetch from PostgreSQL if available
    try:
        repo = service._repository
        if getattr(repo, "available", False):
            from app.database.models.domain import ResearchPlan
            from app.database.models.report import ResearchReport
            from sqlalchemy import select
            db = repo._factory()()
            try:
                plans = db.scalars(
                    select(ResearchPlan).where(ResearchPlan.status.in_(["pending_review", "changes_requested"]))
                ).all()
                for p in plans:
                    items.append({
                        "id": str(p.id),
                        "type": "plan",
                        "session_id": str(p.session_id),
                        "title": p.title or "Research Plan",
                        "status": p.status,
                        "created_at": p.created_at.isoformat() if p.created_at else None,
                    })
                reps = db.scalars(
                    select(ResearchReport).where(ResearchReport.status.in_(["pending_review", "changes_requested"]))
                ).all()
                for r in reps:
                    items.append({
                        "id": str(r.id),
                        "type": "report",
                        "session_id": str(r.session_id),
                        "report_id": str(r.id),
                        "title": r.title or "Research Report",
                        "status": r.status,
                        "created_at": r.created_at.isoformat() if r.created_at else None,
                    })
            finally:
                db.close()
    except Exception:
        pass

    # 2. Check SQLite sessions for runs awaiting approval or marked for review
    try:
        sessions = session_service.list_sessions(user_id=user_id)
        for s in sessions:
            st = str(s.get("status") or "").lower()
            if st in ("plan_approval_required", "awaiting_approval", "pending_review", "changes_requested"):
                items.append({
                    "id": f"plan-{s['session_id']}",
                    "type": "plan",
                    "session_id": s["session_id"],
                    "title": s.get("topic") or "Research Plan",
                    "status": "pending_review",
                    "critic_score": s.get("critic_score"),
                    "created_at": s.get("timestamp"),
                })
    except Exception:
        pass

    return {
        "items": items,
        "count": len(items),
        "total": len(items),
    }


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
