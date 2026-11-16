"""Application service for authenticated plan and report review."""

from __future__ import annotations

from typing import Any, Optional, Sequence

from app.errors import ConflictError, DatabaseError, NotFoundError
from app.observability import get_logger
from app.schemas.enums import ApprovalDecision, ReviewCommentKind
from app.schemas.review import (
    PlanReviewResponse,
    ReportReviewResponse,
    ResearchPlanReviewRecord,
    ReportReviewRecord,
    ReviewAuditRecord,
    ReviewCommentRecord,
    ReviewHistoryResponse,
)

logger = get_logger("app.services.review")


class ResearchReviewService:
    """Coordinates authorization, persistence, and checkpoint resume.

    ``owner_id`` always comes from the authenticated JWT. It is passed to the
    repository on every read/write so a caller cannot review another user's
    session by supplying an identifier.
    """

    def __init__(self, repository: Any, executor: Optional[Any] = None) -> None:
        self._repository = repository
        self._executor = executor

    # ── Plan review ───────────────────────────────────────────────────────

    def get_plan(self, session_id: str, owner_id: str) -> ResearchPlanReviewRecord:
        try:
            row = self._repository.get_plan(session_id, owner_id)
        except Exception as exc:
            raise self._database_error(exc) from exc
        if row is None:
            raise NotFoundError("Research plan not found.")
        return ResearchPlanReviewRecord.model_validate(row)

    def decide_plan(
        self,
        *,
        session_id: str,
        owner_id: str,
        decision: ApprovalDecision,
        comment: Optional[str] = None,
    ) -> PlanReviewResponse:
        try:
            plan_row, audit_row = self._repository.decide_plan(
                session_id=session_id,
                owner_id=owner_id,
                decision=decision.value,
                reviewer_id=owner_id,
                comment=comment,
            )
        except (NotFoundError, ConflictError):
            raise
        except Exception as exc:
            raise self._database_error(exc) from exc

        # A duplicate approval returns no new audit row and must not run the
        # research graph a second time.
        workflow_resumed = False
        if audit_row is not None and self._executor is not None:
            resume = getattr(self._executor, "resume_plan", None)
            if callable(resume):
                try:
                    resume(
                        session_id=session_id,
                        user_id=owner_id,
                        decision=decision.value,
                        comment=comment,
                    )
                    workflow_resumed = True
                except Exception:
                    # The decision is already durably recorded. A missing or
                    # expired checkpoint must not erase the reviewer's action.
                    logger.exception("review.plan_resume_failed")

        return PlanReviewResponse(
            plan=ResearchPlanReviewRecord.model_validate(plan_row),
            audit=ReviewAuditRecord.model_validate(audit_row) if audit_row else None,
            workflow_resumed=workflow_resumed,
        )

    def add_plan_comment(
        self, *, session_id: str, owner_id: str, comment: str
    ) -> ReviewAuditRecord:
        try:
            row = self._repository.add_plan_comment(
                session_id=session_id,
                owner_id=owner_id,
                reviewer_id=owner_id,
                comment=comment,
            )
        except NotFoundError:
            raise
        except Exception as exc:
            raise self._database_error(exc) from exc
        return ReviewAuditRecord.model_validate(row)

    # ── Report review ─────────────────────────────────────────────────────

    def get_report(self, report_id: str, owner_id: str) -> ReportReviewRecord:
        try:
            row = self._repository.get_report(report_id, owner_id)
        except Exception as exc:
            raise self._database_error(exc) from exc
        if row is None:
            raise NotFoundError("Report not found.")
        return ReportReviewRecord.model_validate(row)

    def submit_report(self, report_id: str, owner_id: str) -> ReportReviewResponse:
        try:
            report_row, audit_row = self._repository.submit_report_for_review(
                report_id=report_id,
                owner_id=owner_id,
                reviewer_id=owner_id,
            )
        except (NotFoundError, ConflictError):
            raise
        except Exception as exc:
            raise self._database_error(exc) from exc
        return ReportReviewResponse(
            report=ReportReviewRecord.model_validate(report_row),
            audit=ReviewAuditRecord.model_validate(audit_row) if audit_row else None,
            workflow_resumed=False,
        )

    def decide_report(
        self,
        *,
        report_id: str,
        owner_id: str,
        decision: ApprovalDecision,
        comment: Optional[str] = None,
    ) -> ReportReviewResponse:
        try:
            report_row, audit_row = self._repository.decide_report(
                report_id=report_id,
                owner_id=owner_id,
                decision=decision.value,
                reviewer_id=owner_id,
                comment=comment,
            )
        except (NotFoundError, ConflictError):
            raise
        except Exception as exc:
            raise self._database_error(exc) from exc
        return ReportReviewResponse(
            report=ReportReviewRecord.model_validate(report_row),
            audit=ReviewAuditRecord.model_validate(audit_row) if audit_row else None,
            workflow_resumed=False,
        )

    def add_report_comment(
        self,
        *,
        report_id: str,
        owner_id: str,
        comment: str,
        kind: ReviewCommentKind = ReviewCommentKind.GENERAL,
        claim_ids: Sequence[str] = (),
    ) -> ReviewCommentRecord:
        try:
            row = self._repository.add_report_comment(
                report_id=report_id,
                owner_id=owner_id,
                reviewer_id=owner_id,
                comment=comment,
                kind=kind.value,
                claim_ids=list(claim_ids),
            )
        except NotFoundError:
            raise
        except Exception as exc:
            raise self._database_error(exc) from exc
        return ReviewCommentRecord.model_validate(row)

    def mark_report_revised(
        self,
        *,
        report_id: str,
        owner_id: str,
        comment: Optional[str] = None,
    ) -> ReportReviewResponse:
        try:
            report_row, audit_row = self._repository.mark_report_revised(
                report_id=report_id,
                owner_id=owner_id,
                reviewer_id=owner_id,
                comment=comment,
            )
        except (NotFoundError, ConflictError):
            raise
        except Exception as exc:
            raise self._database_error(exc) from exc
        return ReportReviewResponse(
            report=ReportReviewRecord.model_validate(report_row),
            audit=ReviewAuditRecord.model_validate(audit_row) if audit_row else None,
            workflow_resumed=False,
        )

    # ── Audit history ─────────────────────────────────────────────────────

    def history(self, session_id: str, owner_id: str) -> ReviewHistoryResponse:
        # Reading the plan also enforces session ownership, so an empty audit
        # trail never becomes an information leak for another user's session.
        self.get_plan(session_id, owner_id)
        return self._history(session_id, owner_id)

    def report_history(
        self, report_id: str, owner_id: str
    ) -> ReviewHistoryResponse:
        report = self.get_report(report_id, owner_id)
        return self._history(str(report.session_id), owner_id)

    def _history(self, session_id: str, owner_id: str) -> ReviewHistoryResponse:
        try:
            rows = self._repository.history(session_id, owner_id)
        except Exception as exc:
            raise self._database_error(exc) from exc
        entries = [ReviewAuditRecord.model_validate(row) for row in rows]
        from uuid import UUID

        return ReviewHistoryResponse(
            research_id=UUID(session_id),
            entries=entries,
            count=len(entries),
        )

    @staticmethod
    def _database_error(exc: BaseException) -> DatabaseError:
        logger.warning("review.persistence_failed", exc_info=True)
        return DatabaseError("Review storage is temporarily unavailable.")


__all__ = ["ResearchReviewService"]
