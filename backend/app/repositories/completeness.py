"""
app/repositories/completeness.py
================================
PostgreSQL persistence adapter for research completeness evaluation.

Stores research objectives and completeness evaluation snapshots. All queries
are scoped by ``session_id`` so one user's evaluation can never leak into
another's session. Missing metadata is never fabricated — optional fields stay
NULL.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence

from sqlalchemy import and_, select

from app.config import Settings, get_settings
from app.database.models.completeness import (
    ResearchCompletenessEvaluation,
    ResearchObjective,
)
from app.database.models.domain import ResearchSession
from app.database.session import get_session_factory
from app.observability import get_logger

logger = get_logger("app.repositories.completeness")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _uuid(value: Any) -> Optional[uuid.UUID]:
    if value is None:
        return None
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


class CompletenessRepository:
    """Persist research objectives and completeness evaluations."""

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

    # ── Objectives ──────────────────────────────────────────────────────

    def save_objectives(
        self,
        *,
        session_id: str,
        objectives: Sequence[Mapping[str, Any]],
    ) -> int:
        """Upsert objectives for a session. Returns rows written."""
        session_uuid = _uuid(session_id)
        if session_uuid is None or not objectives:
            return 0

        db = self._factory()()
        written = 0
        try:
            if db.get(ResearchSession, session_uuid) is None:
                return 0

            for item in objectives:
                data = self._mapping(item)
                objective_id = self._uuid(
                    data.get("objective_id") or data.get("id")
                )
                if objective_id is None:
                    objective_id = uuid.uuid4()

                row = db.get(ResearchObjective, objective_id)
                if row is None:
                    row = ResearchObjective(
                        id=objective_id,
                        session_id=session_uuid,
                    )
                    db.add(row)
                    db.flush()

                row.objective_id = self._string(
                    data.get("objective_id") or str(objective_id), 40
                ) or str(objective_id)
                row.description = self._string(
                    data.get("description"), None
                ) or ""
                row.required_evidence = self._string(
                    data.get("required_evidence"), None
                )
                row.status = self._clip(
                    self._value(data.get("status")), 30
                ) or "not_investigated"
                row.related_claim_ids = self._json(
                    data.get("related_claim_ids", [])
                )
                row.related_source_ids = self._json(
                    data.get("related_source_ids", [])
                )
                written += 1

            db.commit()
            return written
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def list_objectives(
        self,
        session_id: str,
        *,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return all objectives for a session, optionally filtered by status."""
        session_uuid = _uuid(session_id)
        if session_uuid is None:
            return []

        db = self._factory()()
        try:
            stmt = select(ResearchObjective).where(
                ResearchObjective.session_id == session_uuid
            )
            if status:
                stmt = stmt.where(ResearchObjective.status == status)
            rows = db.scalars(
                stmt.order_by(ResearchObjective.created_at.asc())
            ).all()
            return [self._objective_to_dict(row) for row in rows]
        finally:
            db.close()

    def get_objective(
        self,
        session_id: str,
        objective_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Return one objective if it belongs to the session."""
        obj_uuid = _uuid(objective_id)
        session_uuid = _uuid(session_id)
        if obj_uuid is None or session_uuid is None:
            return None

        db = self._factory()()
        try:
            row = db.get(ResearchObjective, obj_uuid)
            if row is None or row.session_id != session_uuid:
                return None
            return self._objective_to_dict(row)
        finally:
            db.close()

    # ── Evaluations ─────────────────────────────────────────────────────

    def save_evaluation(
        self,
        *,
        session_id: str,
        overall_status: str,
        completeness_score: float,
        total_objectives: int,
        complete_objectives: int,
        partial_objectives: int,
        insufficient_objectives: int,
        not_investigated_objectives: int,
        blocked_objectives: int,
        gaps: Optional[Sequence[Mapping[str, Any]]] = None,
        recommendations: Optional[Sequence[Mapping[str, Any]]] = None,
        additional_search_tasks: Optional[Sequence[Mapping[str, Any]]] = None,
        iterations_performed: int = 0,
        max_iterations: int = 0,
    ) -> Optional[Dict[str, Any]]:
        """Persist one completeness evaluation snapshot."""
        session_uuid = _uuid(session_id)
        if session_uuid is None:
            return None

        db = self._factory()()
        try:
            if db.get(ResearchSession, session_uuid) is None:
                return None

            row = ResearchCompletenessEvaluation(
                session_id=session_uuid,
                overall_status=overall_status,
                completeness_score=float(completeness_score),
                total_objectives=int(total_objectives),
                complete_objectives=int(complete_objectives),
                partial_objectives=int(partial_objectives),
                insufficient_objectives=int(insufficient_objectives),
                not_investigated_objectives=int(not_investigated_objectives),
                blocked_objectives=int(blocked_objectives),
                gaps_json=self._json(list(gaps or [])),
                recommendations_json=self._json(list(recommendations or [])),
                additional_search_tasks_json=self._json(
                    list(additional_search_tasks or [])
                ),
                iterations_performed=int(iterations_performed),
                max_iterations=int(max_iterations),
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            return self._evaluation_to_dict(row)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def list_evaluations(
        self,
        session_id: str,
        *,
        limit: int = 10,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Return evaluations for a session, newest first."""
        session_uuid = _uuid(session_id)
        if session_uuid is None:
            return []

        db = self._factory()()
        try:
            rows = db.scalars(
                select(ResearchCompletenessEvaluation)
                .where(
                    ResearchCompletenessEvaluation.session_id == session_uuid
                )
                .order_by(
                    ResearchCompletenessEvaluation.created_at.desc()
                )
                .limit(limit)
                .offset(offset)
            ).all()
            return [self._evaluation_to_dict(row) for row in rows]
        finally:
            db.close()

    def get_latest_evaluation(
        self,
        session_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Return the most recent evaluation for a session."""
        session_uuid = _uuid(session_id)
        if session_uuid is None:
            return None

        db = self._factory()()
        try:
            row = db.scalars(
                select(ResearchCompletenessEvaluation)
                .where(
                    ResearchCompletenessEvaluation.session_id == session_uuid
                )
                .order_by(
                    ResearchCompletenessEvaluation.created_at.desc()
                )
                .limit(1)
            ).first()
            return self._evaluation_to_dict(row) if row else None
        finally:
            db.close()

    # ── Helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _mapping(item: Mapping[str, Any] | Any) -> Dict[str, Any]:
        if isinstance(item, Mapping):
            return dict(item)
        if hasattr(item, "model_dump"):
            return dict(item.model_dump(mode="json"))
        return dict(item)

    @staticmethod
    def _string(value: Any, max_len: Optional[int] = None) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        if max_len is not None:
            text = text[:max_len]
        return text or None

    @classmethod
    def _clip(cls, value: Any, limit: int) -> Optional[str]:
        text = cls._string(value)
        return text[:limit] if text is not None else None

    @staticmethod
    def _value(value: Any) -> Any:
        return getattr(value, "value", value)

    @staticmethod
    def _json(value: Any) -> Optional[str]:
        if value is None:
            return None
        try:
            return json.dumps(value, default=str)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _objective_to_dict(row: ResearchObjective) -> Dict[str, Any]:
        return {
            "objective_id": row.objective_id,
            "session_id": str(row.session_id),
            "description": row.description,
            "required_evidence": row.required_evidence,
            "status": row.status,
            "related_claim_ids": (
                json.loads(row.related_claim_ids)
                if row.related_claim_ids
                else []
            ),
            "related_source_ids": (
                json.loads(row.related_source_ids)
                if row.related_source_ids
                else []
            ),
            "created_at": row.created_at.isoformat()
            if row.created_at
            else None,
            "updated_at": row.updated_at.isoformat()
            if row.updated_at
            else None,
        }

    @staticmethod
    def _evaluation_to_dict(
        row: ResearchCompletenessEvaluation,
    ) -> Dict[str, Any]:
        return {
            "evaluation_id": str(row.id),
            "session_id": str(row.session_id),
            "overall_status": row.overall_status,
            "completeness_score": row.completeness_score,
            "total_objectives": row.total_objectives,
            "complete_objectives": row.complete_objectives,
            "partial_objectives": row.partial_objectives,
            "insufficient_objectives": row.insufficient_objectives,
            "not_investigated_objectives": row.not_investigated_objectives,
            "blocked_objectives": row.blocked_objectives,
            "gaps": json.loads(row.gaps_json) if row.gaps_json else [],
            "recommendations": (
                json.loads(row.recommendations_json)
                if row.recommendations_json
                else []
            ),
            "additional_search_tasks": (
                json.loads(row.additional_search_tasks_json)
                if row.additional_search_tasks_json
                else []
            ),
            "iterations_performed": row.iterations_performed,
            "max_iterations": row.max_iterations,
            "created_at": row.created_at.isoformat()
            if row.created_at
            else None,
            "updated_at": row.updated_at.isoformat()
            if row.updated_at
            else None,
        }


class NullCompletenessRepository:
    """No-op adapter for local/test runs without PostgreSQL."""

    available = False

    def save_objectives(self, **_: Any) -> int:
        return 0

    def list_objectives(self, session_id: str, **_: Any) -> List[Dict[str, Any]]:
        return []

    def get_objective(
        self, session_id: str, objective_id: str
    ) -> Optional[Dict[str, Any]]:
        return None

    def save_evaluation(self, **_: Any) -> Optional[Dict[str, Any]]:
        return None

    def list_evaluations(self, session_id: str, **_: Any) -> List[Dict[str, Any]]:
        return []

    def get_latest_evaluation(
        self, session_id: str
    ) -> Optional[Dict[str, Any]]:
        return None


def build_completeness_repository(
    settings: Optional[Settings] = None,
) -> CompletenessRepository | NullCompletenessRepository:
    cfg = settings or get_settings()
    if cfg.database_url:
        return CompletenessRepository(cfg)
    return NullCompletenessRepository()


__all__ = [
    "CompletenessRepository",
    "NullCompletenessRepository",
    "build_completeness_repository",
]
