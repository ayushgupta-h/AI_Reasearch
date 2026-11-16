"""PostgreSQL persistence for plan/report review decisions and audit history.

The repository deliberately keeps review history append-only. Status fields on
``research_plans`` and ``research_reports`` are the current projections; every
decision/comment also writes a ``review_audit`` row with the previous and new
status, reviewer identity, and comment.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from sqlalchemy import select

from app.config import Settings, get_settings
from app.database.models.domain import ResearchPlan, ResearchSession
from app.database.models.report import (
    ReportVersion,
    ResearchReport,
    ReviewAudit,
    ReviewComment,
    ReviewRequest,
)
from app.database.session import get_session_factory
from app.errors import ConflictError, NotFoundError
from app.observability import get_logger
from app.schemas.enums import ApprovalDecision, ReviewAction, ReviewStatus

logger = get_logger("app.repositories.reviews")

_DECISION_STATUS = {
    ApprovalDecision.APPROVED.value: ReviewStatus.APPROVED.value,
    ApprovalDecision.CHANGES_REQUESTED.value: ReviewStatus.CHANGES_REQUESTED.value,
    ApprovalDecision.REJECTED.value: ReviewStatus.REJECTED.value,
}
_PLAN_ACTIONS = {
    ApprovalDecision.APPROVED.value: ReviewAction.PLAN_APPROVED.value,
    ApprovalDecision.CHANGES_REQUESTED.value: ReviewAction.PLAN_CHANGES_REQUESTED.value,
    ApprovalDecision.REJECTED.value: ReviewAction.PLAN_REJECTED.value,
}
_REPORT_ACTIONS = {
    ApprovalDecision.APPROVED.value: ReviewAction.REPORT_APPROVED.value,
    ApprovalDecision.CHANGES_REQUESTED.value: ReviewAction.REPORT_CHANGES_REQUESTED.value,
    ApprovalDecision.REJECTED.value: ReviewAction.REPORT_REJECTED.value,
}


class ReviewRepository:
    """SQLAlchemy adapter for human plan/report review."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self._settings = settings or get_settings()
        self._available = bool(self._settings.database_url)

    @property
    def available(self) -> bool:
        return self._available

    def _factory(self):
        if not self._available:
            raise RuntimeError("PostgreSQL is not configured")
        return get_session_factory(self._settings)

    # ── Plan persistence ──────────────────────────────────────────────────

    def upsert_plan(
        self,
        *,
        session_id: Optional[str],
        owner_id: Optional[str],
        plan: Mapping[str, Any] | Any,
    ) -> Optional[Dict[str, Any]]:
        """Create or update the session's current plan and record submission."""
        session_uuid = self._uuid(session_id)
        owner_uuid = self._uuid(owner_id)
        if session_uuid is None or owner_uuid is None:
            return None
        data = self._mapping(plan)
        db = self._factory()()
        try:
            session = db.get(ResearchSession, session_uuid)
            if session is None or session.owner_id != owner_uuid:
                return None

            row = db.scalar(
                select(ResearchPlan)
                .where(ResearchPlan.session_id == session_uuid)
                .order_by(ResearchPlan.created_at.desc())
            )
            created = row is None
            if row is None:
                row = ResearchPlan(
                    id=uuid.uuid4(),
                    session_id=session_uuid,
                    title=self._text(
                        data.get("title")
                        or data.get("refined_query")
                        or data.get("query")
                        or session.topic,
                        200,
                    )
                    or "Research plan",
                    query=self._text(
                        data.get("query") or data.get("refined_query") or session.topic,
                        20000,
                    )
                    or session.topic,
                    status=ReviewStatus.PENDING_REVIEW.value,
                )
                db.add(row)
                db.flush()

            # A final decision is never silently overwritten by a stale
            # checkpoint. A revised plan creates a new submission/audit entry.
            if not created and row.status in (
                ReviewStatus.APPROVED.value,
                ReviewStatus.REJECTED.value,
            ):
                return self._plan_dict(row)

            row.title = self._text(
                data.get("title") or data.get("refined_query") or row.title, 200
            ) or row.title
            row.objective = self._text(data.get("objective"), 20000)
            row.query = self._text(
                data.get("query") or data.get("refined_query") or row.query, 20000
            ) or row.query
            row.token_budget = int(data.get("token_budget") or 0)
            row.agent_count = max(1, int(data.get("agent_count") or 1))
            try:
                row.details_json = json.dumps(data, default=str)
            except (TypeError, ValueError):
                row.details_json = None
            previous = None if created else row.status
            row.status = ReviewStatus.PENDING_REVIEW.value
            if created or previous != ReviewStatus.PENDING_REVIEW.value:
                db.add(
                    self._audit(
                        session_id=session_uuid,
                        plan_id=row.id,
                        reviewer_id=str(owner_id),
                        action=ReviewAction.PLAN_SUBMITTED.value,
                        previous_status=previous,
                        new_status=ReviewStatus.PENDING_REVIEW.value,
                        comment="Plan submitted for human review.",
                    )
                )
            db.commit()
            return self._plan_dict(row)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def get_plan(self, session_id: str, owner_id: str) -> Optional[Dict[str, Any]]:
        session_uuid = self._session_owned(session_id, owner_id)
        if session_uuid is None:
            return None
        db = self._factory()()
        try:
            row = db.scalar(
                select(ResearchPlan)
                .where(ResearchPlan.session_id == session_uuid)
                .order_by(ResearchPlan.created_at.desc())
            )
            return self._plan_dict(row) if row is not None else None
        finally:
            db.close()

    def decide_plan(
        self,
        *,
        session_id: str,
        owner_id: str,
        decision: str,
        reviewer_id: str,
        comment: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        decision_value = self._value(decision)
        new_status = _DECISION_STATUS.get(decision_value)
        action = _PLAN_ACTIONS.get(decision_value)
        if new_status is None or action is None:
            raise ValueError("Unsupported plan review decision")

        session_uuid = self._session_owned(session_id, owner_id)
        if session_uuid is None:
            raise NotFoundError("Research session not found.")
        db = self._factory()()
        try:
            row = db.scalar(
                select(ResearchPlan)
                .where(ResearchPlan.session_id == session_uuid)
                .order_by(ResearchPlan.created_at.desc())
                .with_for_update()
            )
            if row is None:
                raise NotFoundError("Research plan not found.")

            # Idempotent duplicate approval: return the accepted plan without
            # appending a second audit row.
            if row.status == new_status:
                # No new audit entry and no workflow resume for a duplicate
                # approval. Returning the existing projection is idempotent.
                return self._plan_dict(row), None

            if row.status in (
                ReviewStatus.APPROVED.value,
                ReviewStatus.REJECTED.value,
            ):
                raise ConflictError(
                    "The plan already has a final review decision."
                )

            previous = row.status
            row.status = new_status
            row.reviewed_by = str(reviewer_id)
            row.reviewed_at = datetime.now(timezone.utc)
            row.review_note = comment
            audit = self._audit(
                session_id=session_uuid,
                plan_id=row.id,
                reviewer_id=str(reviewer_id),
                action=action,
                previous_status=previous,
                new_status=new_status,
                comment=comment,
            )
            db.add(audit)
            db.commit()
            db.refresh(audit)
            return self._plan_dict(row), self._audit_dict(audit)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def add_plan_comment(
        self,
        *,
        session_id: str,
        owner_id: str,
        reviewer_id: str,
        comment: str,
    ) -> Dict[str, Any]:
        """Attach a reviewer comment to the current plan review."""
        session_uuid = self._session_owned(session_id, owner_id)
        if session_uuid is None:
            raise NotFoundError("Research session not found.")
        db = self._factory()()
        try:
            plan = db.scalar(
                select(ResearchPlan)
                .where(ResearchPlan.session_id == session_uuid)
                .order_by(ResearchPlan.created_at.desc())
            )
            if plan is None:
                raise NotFoundError("Research plan not found.")
            audit = self._audit(
                session_id=session_uuid,
                plan_id=plan.id,
                reviewer_id=str(reviewer_id),
                action=ReviewAction.COMMENT_ADDED.value,
                previous_status=plan.status,
                new_status=plan.status,
                comment=comment,
            )
            db.add(audit)
            db.commit()
            db.refresh(audit)
            return self._audit_dict(audit)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    # ── Report persistence ────────────────────────────────────────────────

    def get_report(self, report_id: str, owner_id: str) -> Optional[Dict[str, Any]]:
        report_uuid = self._uuid(report_id)
        if report_uuid is None:
            return None
        db = self._factory()()
        try:
            report = db.get(ResearchReport, report_uuid)
            if report is None:
                return None
            session = db.get(ResearchSession, report.session_id)
            if session is None or str(session.owner_id) != str(owner_id):
                return None
            latest = db.scalar(
                select(ReviewRequest)
                .where(ReviewRequest.report_id == report.id)
                .order_by(ReviewRequest.created_at.desc())
            )
            return self._report_dict(report, latest)
        finally:
            db.close()

    def submit_report_for_review(
        self,
        *,
        report_id: str,
        owner_id: str,
        reviewer_id: str,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Move a report into the ``pending_review`` state and audit it."""
        report_uuid = self._uuid(report_id)
        if report_uuid is None:
            raise NotFoundError("Report not found.")
        db = self._factory()()
        try:
            report = db.get(ResearchReport, report_uuid)
            if report is None:
                raise NotFoundError("Report not found.")
            session = db.get(ResearchSession, report.session_id)
            if session is None or str(session.owner_id) != str(owner_id):
                raise NotFoundError("Report not found.")
            if report.status == ReviewStatus.PENDING_REVIEW.value:
                return self._report_dict(report, None), None
            previous = report.status
            report.status = ReviewStatus.PENDING_REVIEW.value
            request = ReviewRequest(
                id=uuid.uuid4(),
                report_id=report.id,
                reviewer_id=self._uuid(reviewer_id),
                status=ReviewStatus.PENDING_REVIEW.value,
                previous_status=previous,
            )
            db.add(request)
            audit = self._audit(
                session_id=report.session_id,
                report_id=report.id,
                reviewer_id=str(reviewer_id),
                action=ReviewAction.REPORT_SUBMITTED.value,
                previous_status=previous,
                new_status=ReviewStatus.PENDING_REVIEW.value,
                comment="Report submitted for human review.",
            )
            db.add(audit)
            db.commit()
            db.refresh(request)
            db.refresh(audit)
            return self._report_dict(report, request), self._audit_dict(audit)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def decide_report(
        self,
        *,
        report_id: str,
        owner_id: str,
        decision: str,
        reviewer_id: str,
        comment: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        decision_value = self._value(decision)
        new_status = _DECISION_STATUS.get(decision_value)
        action = _REPORT_ACTIONS.get(decision_value)
        if new_status is None or action is None:
            raise ValueError("Unsupported report review decision")
        report_uuid = self._uuid(report_id)
        if report_uuid is None:
            raise NotFoundError("Report not found.")

        db = self._factory()()
        try:
            report = db.get(ResearchReport, report_uuid)
            if report is None:
                raise NotFoundError("Report not found.")
            session = db.get(ResearchSession, report.session_id)
            if session is None or str(session.owner_id) != str(owner_id):
                raise NotFoundError("Report not found.")

            if report.status == new_status:
                latest = db.scalar(
                    select(ReviewRequest)
                    .where(ReviewRequest.report_id == report.id)
                    .order_by(ReviewRequest.created_at.desc())
                )
                return self._report_dict(report, latest), None
            if report.status in (
                ReviewStatus.APPROVED.value,
                ReviewStatus.REJECTED.value,
            ):
                raise ConflictError("The report already has a final review decision.")

            previous = report.status
            report.status = new_status
            version = db.scalar(
                select(ReportVersion)
                .where(ReportVersion.report_id == report.id)
                .order_by(ReportVersion.version_number.desc())
            )
            if version is not None:
                version.status = new_status
            request = ReviewRequest(
                id=uuid.uuid4(),
                report_id=report.id,
                reviewer_id=self._uuid(reviewer_id),
                status=new_status,
                previous_status=previous,
                decision=decision_value,
                decided_at=datetime.now(timezone.utc),
                reviewer_note=comment,
            )
            db.add(request)
            audit = self._audit(
                session_id=report.session_id,
                report_id=report.id,
                reviewer_id=str(reviewer_id),
                action=action,
                previous_status=previous,
                new_status=new_status,
                comment=comment,
            )
            db.add(audit)
            db.commit()
            db.refresh(request)
            db.refresh(audit)
            return self._report_dict(report, request), self._audit_dict(audit)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def add_report_comment(
        self,
        *,
        report_id: str,
        owner_id: str,
        reviewer_id: str,
        comment: str,
        kind: str = "general",
        claim_ids: Sequence[str] = (),
    ) -> Dict[str, Any]:
        report_uuid = self._uuid(report_id)
        if report_uuid is None:
            raise NotFoundError("Report not found.")
        db = self._factory()()
        try:
            report = db.get(ResearchReport, report_uuid)
            if report is None:
                raise NotFoundError("Report not found.")
            session = db.get(ResearchSession, report.session_id)
            if session is None or str(session.owner_id) != str(owner_id):
                raise NotFoundError("Report not found.")

            request = db.scalar(
                select(ReviewRequest)
                .where(ReviewRequest.report_id == report.id)
                .order_by(ReviewRequest.created_at.desc())
            )
            if request is None:
                request = ReviewRequest(
                    id=uuid.uuid4(),
                    report_id=report.id,
                    reviewer_id=self._uuid(reviewer_id),
                    status=ReviewStatus.PENDING_REVIEW.value,
                    previous_status=report.status,
                )
                db.add(request)
                db.flush()

            row = ReviewComment(
                id=uuid.uuid4(),
                review_request_id=request.id,
                author_id=self._uuid(reviewer_id),
                body=comment,
                kind=kind,
                claim_ids_json=json.dumps(list(claim_ids)),
            )
            db.add(row)
            audit = self._audit(
                session_id=report.session_id,
                report_id=report.id,
                reviewer_id=str(reviewer_id),
                action=ReviewAction.COMMENT_ADDED.value,
                previous_status=report.status,
                new_status=report.status,
                comment=comment,
            )
            db.add(audit)
            db.commit()
            db.refresh(row)
            return self._comment_dict(row)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def mark_report_revised(
        self,
        *,
        report_id: str,
        owner_id: str,
        reviewer_id: str,
        comment: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        report_uuid = self._uuid(report_id)
        if report_uuid is None:
            raise NotFoundError("Report not found.")
        db = self._factory()()
        try:
            report = db.get(ResearchReport, report_uuid)
            if report is None:
                raise NotFoundError("Report not found.")
            session = db.get(ResearchSession, report.session_id)
            if session is None or str(session.owner_id) != str(owner_id):
                raise NotFoundError("Report not found.")
            previous = report.status
            report.status = ReviewStatus.REVISED.value
            audit = self._audit(
                session_id=report.session_id,
                report_id=report.id,
                reviewer_id=str(reviewer_id),
                action=ReviewAction.REPORT_REVISED.value,
                previous_status=previous,
                new_status=ReviewStatus.REVISED.value,
                comment=comment,
            )
            db.add(audit)
            db.commit()
            db.refresh(audit)
            return self._report_dict(report, None), self._audit_dict(audit)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    # ── History ───────────────────────────────────────────────────────────

    def history(self, session_id: str, owner_id: str) -> List[Dict[str, Any]]:
        session_uuid = self._session_owned(session_id, owner_id)
        if session_uuid is None:
            return []
        db = self._factory()()
        try:
            rows = db.scalars(
                select(ReviewAudit)
                .where(ReviewAudit.session_id == session_uuid)
                .order_by(ReviewAudit.created_at.asc())
            ).all()
            return [self._audit_dict(row) for row in rows]
        finally:
            db.close()

    # ── Helpers ───────────────────────────────────────────────────────────

    def _session_owned(self, session_id: str, owner_id: str) -> Optional[uuid.UUID]:
        session_uuid = self._uuid(session_id)
        if session_uuid is None:
            return None
        db = self._factory()()
        try:
            session = db.get(ResearchSession, session_uuid)
            if session is None or str(session.owner_id) != str(owner_id):
                return None
            return session_uuid
        finally:
            db.close()

    def _audit(
        self,
        *,
        session_id: uuid.UUID,
        reviewer_id: str,
        action: str,
        new_status: str,
        previous_status: Optional[str] = None,
        plan_id: Optional[uuid.UUID] = None,
        report_id: Optional[uuid.UUID] = None,
        comment: Optional[str] = None,
    ) -> ReviewAudit:
        return ReviewAudit(
            id=uuid.uuid4(),
            session_id=session_id,
            plan_id=plan_id,
            report_id=report_id,
            reviewer_id=str(reviewer_id),
            action=action,
            previous_status=previous_status,
            new_status=new_status,
            comment=comment,
        )

    def _latest_audit_dict(
        self,
        db: Any,
        session_id: uuid.UUID,
        *,
        plan_id: Optional[uuid.UUID] = None,
        report_id: Optional[uuid.UUID] = None,
    ) -> Optional[Dict[str, Any]]:
        query = select(ReviewAudit).where(ReviewAudit.session_id == session_id)
        if plan_id is not None:
            query = query.where(ReviewAudit.plan_id == plan_id)
        if report_id is not None:
            query = query.where(ReviewAudit.report_id == report_id)
        row = db.scalar(query.order_by(ReviewAudit.created_at.desc()))
        return self._audit_dict(row) if row is not None else None

    @staticmethod
    def _plan_dict(row: ResearchPlan) -> Dict[str, Any]:
        return {
            "plan_id": row.id,
            "session_id": row.session_id,
            "title": row.title,
            "objective": row.objective,
            "query": row.query,
            "token_budget": row.token_budget,
            "agent_count": row.agent_count,
            "status": row.status,
            "review_note": row.review_note,
            "reviewed_by": row.reviewed_by,
            "reviewed_at": ReviewRepository._aware(row.reviewed_at),
            "details": ReviewRepository._details(row),
            "created_at": ReviewRepository._aware(row.created_at),
            "updated_at": ReviewRepository._aware(row.updated_at),
        }

    @staticmethod
    def _details(row: ResearchPlan) -> Dict[str, Any]:
        try:
            value = json.loads(row.details_json or "{}")
            return value if isinstance(value, dict) else {}
        except (TypeError, ValueError):
            return {}

    @staticmethod
    def _report_dict(
        report: ResearchReport, latest: Optional[ReviewRequest]
    ) -> Dict[str, Any]:
        return {
            "report_id": report.id,
            "session_id": report.session_id,
            "title": report.title,
            "status": report.status,
            "critic_score": report.score,
            "latest_decision": latest.decision if latest else None,
            "latest_comment": latest.reviewer_note if latest else None,
            "reviewer_id": str(latest.reviewer_id) if latest and latest.reviewer_id else None,
            "decided_at": ReviewRepository._aware(latest.decided_at) if latest else None,
            "created_at": ReviewRepository._aware(report.created_at),
            "updated_at": ReviewRepository._aware(report.updated_at),
        }

    @staticmethod
    def _comment_dict(row: ReviewComment) -> Dict[str, Any]:
        try:
            claim_ids = json.loads(row.claim_ids_json or "[]")
        except (TypeError, ValueError):
            claim_ids = []
        return {
            "comment_id": row.id,
            "review_request_id": row.review_request_id,
            "author_id": str(row.author_id) if row.author_id else None,
            "body": row.body,
            "kind": row.kind,
            "claim_ids": claim_ids,
            "is_resolved": row.is_resolved,
            "created_at": ReviewRepository._aware(row.created_at),
        }

    @staticmethod
    def _audit_dict(row: ReviewAudit) -> Dict[str, Any]:
        return {
            "audit_id": row.id,
            "session_id": row.session_id,
            "plan_id": row.plan_id,
            "report_id": row.report_id,
            "reviewer_id": row.reviewer_id,
            "action": row.action,
            "previous_status": row.previous_status,
            "new_status": row.new_status,
            "comment": row.comment,
            "created_at": ReviewRepository._aware(row.created_at),
        }

    @staticmethod
    def _aware(value: Optional[datetime]) -> Optional[datetime]:
        if value is None:
            return None
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _mapping(item: Mapping[str, Any] | Any) -> Dict[str, Any]:
        if isinstance(item, Mapping):
            return dict(item)
        if hasattr(item, "model_dump"):
            return dict(item.model_dump(mode="json"))
        return dict(item)

    @staticmethod
    def _value(value: Any) -> str:
        return str(getattr(value, "value", value))

    @staticmethod
    def _text(value: Any, limit: Optional[int] = None) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        return text[:limit] if limit else text

    @classmethod
    def _uuid(cls, value: Any) -> Optional[uuid.UUID]:
        if value is None:
            return None
        try:
            return uuid.UUID(str(value))
        except (ValueError, TypeError, AttributeError):
            return None


class NullReviewRepository:
    """No-op adapter used when PostgreSQL is not configured."""

    available = False

    def _empty(self, *args: Any, **kwargs: Any) -> Any:
        return None

    upsert_plan = _empty
    get_plan = _empty
    get_report = _empty
    history = lambda self, *a, **k: []

    def decide_plan(self, **_: Any):
        raise RuntimeError("PostgreSQL is required for plan review.")

    def add_plan_comment(self, **_: Any):
        raise RuntimeError("PostgreSQL is required for plan review.")

    def submit_report_for_review(self, **_: Any):
        raise RuntimeError("PostgreSQL is required for report review.")

    def decide_report(self, **_: Any):
        raise RuntimeError("PostgreSQL is required for report review.")

    def add_report_comment(self, **_: Any):
        raise RuntimeError("PostgreSQL is required for report review.")

    def mark_report_revised(self, **_: Any):
        raise RuntimeError("PostgreSQL is required for report review.")


def build_review_repository(
    settings: Optional[Settings] = None,
) -> ReviewRepository | NullReviewRepository:
    cfg = settings or get_settings()
    if cfg.database_url:
        return ReviewRepository(cfg)
    return NullReviewRepository()


__all__ = [
    "NullReviewRepository",
    "ReviewRepository",
    "build_review_repository",
]
