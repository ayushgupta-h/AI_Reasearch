"""PostgreSQL adapter for contradiction analyses."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence

from sqlalchemy import and_, or_, select

from app.config import Settings, get_settings
from app.database.models.domain import ResearchSession
from app.database.models.research import (
    ContradictionEvidence,
    ResearchClaim,
    ResearchContradiction,
    ResearchEvidence,
)
from app.database.session import get_session_factory
from app.observability import get_logger

logger = get_logger("app.repositories.contradictions")


class ContradictionRepository:
    """Store candidate contradictions without resolving scientific disputes.

    The repository is deliberately conservative: it only links claims and
    evidence rows that already exist. Missing rows are left to the future
    claim/evidence materialization step rather than being fabricated here.
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

    def save_analyses(
        self,
        *,
        session_id: Optional[str],
        analyses: Sequence[Mapping[str, Any] | Any],
    ) -> int:
        """Upsert analyses for one research session and return rows written."""
        session_uuid = self._uuid(session_id)
        if session_uuid is None or not analyses:
            return 0

        session = self._factory()()
        written = 0
        try:
            if session.get(ResearchSession, session_uuid) is None:
                return 0
            for item in analyses:
                data = self._mapping(item)
                claim_a_id = self._uuid(data.get("claim_a_id"))
                claim_b_id = self._uuid(data.get("claim_b_id"))
                if claim_a_id is None or claim_b_id is None or claim_a_id == claim_b_id:
                    continue
                if session.get(ResearchClaim, claim_a_id) is None:
                    continue
                if session.get(ResearchClaim, claim_b_id) is None:
                    continue

                row = session.scalar(
                    select(ResearchContradiction).where(
                        ResearchContradiction.session_id == session_uuid,
                        or_(
                            and_(
                                ResearchContradiction.claim_a_id == claim_a_id,
                                ResearchContradiction.claim_b_id == claim_b_id,
                            ),
                            and_(
                                ResearchContradiction.claim_a_id == claim_b_id,
                                ResearchContradiction.claim_b_id == claim_a_id,
                            ),
                        ),
                    )
                )
                if row is None:
                    row = ResearchContradiction(
                        session_id=session_uuid,
                        claim_a_id=claim_a_id,
                        claim_b_id=claim_b_id,
                    )
                    session.add(row)
                    session.flush()

                row.category = self._clip(self._value(data.get("category")), 40) or "insufficient_information"
                row.severity = self._clip(self._value(data.get("severity")), 20) or "medium"
                row.status = self._clip(self._value(data.get("status")), 30) or "unresolved"
                row.description = self._string(data.get("description"))
                row.analysis_json = json.dumps(
                    data, ensure_ascii=False, default=str
                )
                self._sync_evidence(
                    session,
                    row,
                    data.get("claim_a_evidence_ids", []),
                    side="a",
                )
                self._sync_evidence(
                    session,
                    row,
                    data.get("claim_b_evidence_ids", []),
                    side="b",
                )
                written += 1
            session.commit()
            return written
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _sync_evidence(
        self,
        session: Any,
        contradiction: ResearchContradiction,
        evidence_ids: Any,
        *,
        side: str,
    ) -> None:
        existing = {
            link.evidence_id
            for link in session.scalars(
                select(ContradictionEvidence).where(
                    ContradictionEvidence.contradiction_id == contradiction.id
                )
            ).all()
        }
        for raw in evidence_ids or []:
            evidence_id = self._uuid(raw)
            if evidence_id is None or evidence_id in existing:
                continue
            evidence = session.get(ResearchEvidence, evidence_id)
            if evidence is None:
                continue
            # Evidence must belong to the same research session as the claim;
            # this prevents cross-session evidence links.
            if evidence.session_id != contradiction.session_id:
                continue
            link = ContradictionEvidence(
                contradiction_id=contradiction.id,
                evidence_id=evidence_id,
                side=side,
                created_at=datetime.now(timezone.utc),
            )
            session.add(link)
            existing.add(evidence_id)

    def list_for_session(self, session_id: str) -> list[dict[str, Any]]:
        session_uuid = self._uuid(session_id)
        if session_uuid is None:
            return []
        session = self._factory()()
        try:
            rows = session.scalars(
                select(ResearchContradiction)
                .where(ResearchContradiction.session_id == session_uuid)
                .order_by(ResearchContradiction.created_at.asc())
            ).all()
            return [
                {
                    "contradiction_id": str(row.id),
                    "session_id": str(row.session_id),
                    "claim_a_id": str(row.claim_a_id),
                    "claim_b_id": str(row.claim_b_id),
                    "category": row.category,
                    "severity": row.severity,
                    "status": row.status,
                    "description": row.description,
                    "resolution_notes": row.resolution_notes,
                    "evidence": [
                        {
                            "evidence_id": str(link.evidence_id),
                            "side": link.side,
                            "weight": link.weight,
                        }
                        for link in row.evidence_links
                    ],
                }
                for row in rows
            ]
        finally:
            session.close()

    @staticmethod
    def _mapping(item: Mapping[str, Any] | Any) -> dict[str, Any]:
        if isinstance(item, Mapping):
            return dict(item)
        if hasattr(item, "model_dump"):
            return dict(item.model_dump(mode="json"))
        return dict(item)

    @staticmethod
    def _value(value: Any) -> Any:
        return getattr(value, "value", value)

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
    def _uuid(value: Any) -> Optional[uuid.UUID]:
        if value is None:
            return None
        try:
            return uuid.UUID(str(value))
        except (ValueError, TypeError, AttributeError):
            return None


class NullContradictionRepository:
    """No-op adapter for local/test runs without PostgreSQL."""

    available = False

    def save_analyses(self, **_: Any) -> int:
        return 0

    def list_for_session(self, session_id: str) -> list[dict[str, Any]]:
        return []


def build_contradiction_repository(
    settings: Optional[Settings] = None,
) -> ContradictionRepository | NullContradictionRepository:
    cfg = settings or get_settings()
    if cfg.database_url:
        return ContradictionRepository(cfg)
    return NullContradictionRepository()


__all__ = [
    "ContradictionRepository",
    "NullContradictionRepository",
    "build_contradiction_repository",
]
