"""Persistence adapter for source-credibility evaluations."""

from __future__ import annotations

import json
import uuid
from typing import Any, Mapping, Optional, Sequence

from sqlalchemy import select

from app.config import Settings, get_settings
from app.database.models.credibility import SourceCredibilityEvaluation as EvaluationRow
from app.database.models.research import ResearchSource
from app.database.session import get_session_factory
from app.observability import get_logger

logger = get_logger("app.repositories.source_credibility")


class SourceCredibilityRepository:
    """Upsert credibility evaluations into PostgreSQL.

    Writes are idempotent per ``(research_id, source_url)`` and never abort the
    research pipeline. A source row is linked only when it already exists; the
    evaluator does not fabricate a foreign-key target.
    """

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

    def save_evaluations(
        self,
        *,
        research_id: Optional[str],
        evaluations: Sequence[Mapping[str, Any] | Any],
    ) -> int:
        """Store evaluations and return the number written."""
        if not evaluations or not research_id:
            return 0
        session = self._factory()()
        written = 0
        try:
            for item in evaluations:
                data = self._mapping(item)
                url = self._clip(data.get("source_url") or data.get("url"), 2048)
                if not url:
                    continue
                source_id = self._existing_source_id(session, data.get("source_id"))
                row = session.scalar(
                    select(EvaluationRow).where(
                        EvaluationRow.research_id == research_id,
                        EvaluationRow.source_url == url,
                    )
                )
                if row is None:
                    row = EvaluationRow(
                        research_id=research_id,
                        source_url=url,
                    )
                    session.add(row)
                row.source_id = source_id
                row.title = self._clip(data.get("title"), 500)
                row.publisher = self._clip(data.get("publisher"), 200)
                row.source_type = self._clip(data.get("source_type"), 40)
                row.score = data.get("score")
                row.confidence = float(data.get("confidence") or 0.0)
                row.label = self._clip(
                    data.get("label") or "insufficient_information", 40
                ) or "insufficient_information"
                row.factors_json = json.dumps(
                    list(data.get("factors") or []), ensure_ascii=False, default=str
                )
                row.missing_fields_json = json.dumps(
                    list(data.get("missing_fields") or []),
                    ensure_ascii=False,
                    default=str,
                )
                row.metadata_json = json.dumps(
                    dict(data.get("metadata") or {}),
                    ensure_ascii=False,
                    default=str,
                )
                row.method = self._clip(data.get("method") or "heuristic_v1", 40) or "heuristic_v1"
                written += 1
            session.commit()
            return written
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def list_for_research(
        self, research_id: str
    ) -> list[dict[str, Any]]:
        """Return persisted evaluations for one run."""
        session = self._factory()()
        try:
            rows = session.scalars(
                select(EvaluationRow)
                .where(EvaluationRow.research_id == research_id)
                .order_by(EvaluationRow.created_at.asc())
            ).all()
            return [
                {
                    "source_id": str(row.source_id) if row.source_id else None,
                    "research_id": row.research_id,
                    "source_url": row.source_url,
                    "title": row.title,
                    "publisher": row.publisher,
                    "source_type": row.source_type,
                    "score": row.score,
                    "confidence": row.confidence,
                    "label": row.label,
                    "factors": json.loads(row.factors_json or "[]"),
                    "missing_fields": json.loads(row.missing_fields_json or "[]"),
                    "metadata": json.loads(row.metadata_json or "{}"),
                    "method": row.method,
                }
                for row in rows
            ]
        finally:
            session.close()

    # ── Helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _mapping(item: Mapping[str, Any] | Any) -> dict[str, Any]:
        if isinstance(item, Mapping):
            return dict(item)
        if hasattr(item, "model_dump"):
            return dict(item.model_dump(mode="json"))
        return dict(item)

    @staticmethod
    def _string(value: Any) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @classmethod
    def _clip(cls, value: Any, limit: int) -> Optional[str]:
        text = cls._string(value)
        return text[:limit] if text is not None else None

    @staticmethod
    def _existing_source_id(session: Any, value: Any) -> Optional[uuid.UUID]:
        if not value:
            return None
        try:
            source_id = uuid.UUID(str(value))
        except (ValueError, TypeError, AttributeError):
            return None
        if session.get(ResearchSource, source_id) is None:
            return None
        return source_id


class NullSourceCredibilityRepository:
    """No-op adapter for local/test runs without PostgreSQL."""

    available = False

    def save_evaluations(self, **_: Any) -> int:
        return 0

    def list_for_research(self, research_id: str) -> list[dict[str, Any]]:
        return []


def build_source_credibility_repository(
    settings: Optional[Settings] = None,
) -> SourceCredibilityRepository | NullSourceCredibilityRepository:
    cfg = settings or get_settings()
    if cfg.database_url:
        return SourceCredibilityRepository(cfg)
    return NullSourceCredibilityRepository()


__all__ = [
    "NullSourceCredibilityRepository",
    "SourceCredibilityRepository",
    "build_source_credibility_repository",
]
