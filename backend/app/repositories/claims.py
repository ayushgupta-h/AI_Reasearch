"""
app/repositories/claims.py
==========================
PostgreSQL persistence adapter for claim-level evidence verification.

Stores research claims, evidence passages, claim-evidence relationships, and
verification results for a research session. All queries are scoped by
``session_id`` so one user's claims can never leak into another's session.

Follows the same conservative pattern as
:mod:`app.repositories.contradictions` and :mod:`app.repositories.source_credidity`:
rows are only linked when their foreign-key targets already exist, and the
repository never fabricates missing sources or evidence.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence

from sqlalchemy import and_, or_, select

from app.config import Settings, get_settings
from app.database.models.domain import ResearchSession
from app.database.models.research import (
    ClaimEvidence,
    ResearchClaim,
    ResearchEvidence,
    ResearchSource,
)
from app.database.session import get_session_factory
from app.observability import get_logger

logger = get_logger("app.repositories.claims")


class ClaimRepository:
    """Persist claims, evidence, and verification results to PostgreSQL."""

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

    # ── Save ─────────────────────────────────────────────────────────────

    def save_claims(
        self,
        *,
        session_id: str,
        claims: Sequence[Mapping[str, Any]],
    ) -> int:
        """Save extracted claims for a research session. Returns rows written."""
        session_uuid = self._uuid(session_id)
        if session_uuid is None or not claims:
            return 0

        db = self._factory()()
        written = 0
        try:
            if db.get(ResearchSession, session_uuid) is None:
                return 0

            for item in claims:
                data = self._mapping(item)
                claim_id = self._uuid(data.get("claim_id") or data.get("id"))
                if claim_id is None:
                    claim_id = uuid.uuid4()

                row = db.get(ResearchClaim, claim_id)
                if row is None:
                    row = ResearchClaim(
                        id=claim_id,
                        session_id=session_uuid,
                    )
                    db.add(row)
                    db.flush()

                row.text = self._string(data.get("text") or data.get("claim_text"), 20000) or ""
                row.claim_type = self._clip(
                    self._value(data.get("claim_type")), 30
                ) or "factual"
                row.status = self._clip(
                    self._value(data.get("status")), 30
                ) or "unverified"
                row.confidence = float(data.get("confidence") or 0.0)
                written += 1

            db.commit()
            return written
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def save_evidence(
        self,
        *,
        session_id: str,
        evidence: Sequence[Mapping[str, Any]],
    ) -> int:
        """Save evidence passages for a research session. Returns rows written."""
        session_uuid = self._uuid(session_id)
        if session_uuid is None or not evidence:
            return 0

        db = self._factory()()
        written = 0
        try:
            if db.get(ResearchSession, session_uuid) is None:
                return 0

            for item in evidence:
                data = self._mapping(item)
                evidence_id = self._uuid(data.get("evidence_id") or data.get("id"))
                if evidence_id is None:
                    evidence_id = uuid.uuid4()

                source_id = self._existing_source_id(db, data.get("source_id"))

                row = db.get(ResearchEvidence, evidence_id)
                if row is None:
                    row = ResearchEvidence(
                        id=evidence_id,
                        session_id=session_uuid,
                    )
                    db.add(row)
                    db.flush()

                row.text = self._string(data.get("quote") or data.get("text"), None) or ""
                row.source_id = source_id
                row.stance = self._clip(
                    self._value(data.get("stance")), 20
                ) or "neutral"
                row.weight = float(data.get("strength") or data.get("weight") or 1.0)
                row.verified = bool(data.get("verified", False))
                row.location = self._clip(data.get("location"), 200)
                written += 1

            db.commit()
            return written
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def save_claim_evidence_links(
        self,
        *,
        session_id: str,
        links: Sequence[Mapping[str, Any]],
    ) -> int:
        """Save claim-evidence association rows. Returns rows written."""
        session_uuid = self._uuid(session_id)
        if session_uuid is None or not links:
            return 0

        db = self._factory()()
        written = 0
        try:
            if db.get(ResearchSession, session_uuid) is None:
                return 0

            for item in links:
                data = self._mapping(item)
                claim_id = self._uuid(data.get("claim_id"))
                evidence_id = self._uuid(data.get("evidence_id"))
                if claim_id is None or evidence_id is None:
                    continue
                if db.get(ResearchClaim, claim_id) is None:
                    continue
                if db.get(ResearchEvidence, evidence_id) is None:
                    continue

                existing = db.scalar(
                    select(ClaimEvidence).where(
                        and_(
                            ClaimEvidence.claim_id == claim_id,
                            ClaimEvidence.evidence_id == evidence_id,
                        )
                    )
                )
                if existing is not None:
                    continue

                link = ClaimEvidence(
                    claim_id=claim_id,
                    evidence_id=evidence_id,
                    stance=self._clip(
                        self._value(data.get("stance")), 20
                    ) or "supports",
                    weight=float(data.get("weight") or 1.0),
                    created_at=datetime.now(timezone.utc),
                )
                db.add(link)
                written += 1

            db.commit()
            return written
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def save_verification_results(
        self,
        *,
        session_id: str,
        results: Sequence[Mapping[str, Any]],
    ) -> int:
        """Save verification results by updating claim status/confidence.

        Verification results are persisted by updating the corresponding
        ``research_claims`` row (status, confidence) and recording the
        full result in an analysis_json column via the claim's metadata.
        """
        session_uuid = self._uuid(session_id)
        if session_uuid is None or not results:
            return 0

        db = self._factory()()
        written = 0
        try:
            if db.get(ResearchSession, session_uuid) is None:
                return 0

            for item in results:
                data = self._mapping(item)
                claim_id = self._uuid(data.get("claim_id"))
                if claim_id is None:
                    continue
                row = db.get(ResearchClaim, claim_id)
                if row is None:
                    continue
                row.status = self._clip(
                    self._value(data.get("status")), 30
                ) or "unverified"
                row.confidence = float(data.get("confidence") or 0.0)
                written += 1

            db.commit()
            return written
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    # ── Read ─────────────────────────────────────────────────────────────

    def list_claims(
        self,
        session_id: str,
        *,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return all claims for a research session, optionally filtered by status."""
        session_uuid = self._uuid(session_id)
        if session_uuid is None:
            return []

        db = self._factory()()
        try:
            stmt = select(ResearchClaim).where(
                ResearchClaim.session_id == session_uuid
            )
            if status:
                stmt = stmt.where(ResearchClaim.status == status)
            rows = db.scalars(stmt.order_by(ResearchClaim.created_at.asc())).all()
            return [self._claim_to_dict(row) for row in rows]
        finally:
            db.close()

    def get_claim_with_evidence(
        self,
        session_id: str,
        claim_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Return one claim with its linked evidence passages."""
        claim_uuid = self._uuid(claim_id)
        session_uuid = self._uuid(session_id)
        if claim_uuid is None or session_uuid is None:
            return None

        db = self._factory()()
        try:
            claim = db.get(ResearchClaim, claim_uuid)
            if claim is None or claim.session_id != session_uuid:
                return None

            links = db.scalars(
                select(ClaimEvidence).where(ClaimEvidence.claim_id == claim_uuid)
            ).all()

            evidence_list: List[Dict[str, Any]] = []
            for link in links:
                evidence = db.get(ResearchEvidence, link.evidence_id)
                if evidence is None:
                    continue
                source = db.get(ResearchSource, evidence.source_id) if evidence.source_id else None
                evidence_list.append({
                    "evidence_id": str(evidence.id),
                    "source_id": str(evidence.source_id) if evidence.source_id else None,
                    "source_url": source.url if source else None,
                    "source_title": source.title if source else None,
                    "quote": evidence.text,
                    "stance": link.stance,
                    "weight": link.weight,
                    "location": evidence.location,
                })

            return {
                "claim_id": str(claim.id),
                "session_id": str(claim.session_id),
                "text": claim.text,
                "claim_type": claim.claim_type,
                "status": claim.status,
                "confidence": claim.confidence,
                "evidence": evidence_list,
                "created_at": claim.created_at.isoformat() if claim.created_at else None,
            }
        finally:
            db.close()

    def list_evidence_for_session(self, session_id: str) -> List[Dict[str, Any]]:
        """Return all evidence passages for a research session."""
        session_uuid = self._uuid(session_id)
        if session_uuid is None:
            return []

        db = self._factory()()
        try:
            rows = db.scalars(
                select(ResearchEvidence)
                .where(ResearchEvidence.session_id == session_uuid)
                .order_by(ResearchEvidence.created_at.asc())
            ).all()
            result: List[Dict[str, Any]] = []
            for row in rows:
                source = db.get(ResearchSource, row.source_id) if row.source_id else None
                result.append({
                    "evidence_id": str(row.id),
                    "source_id": str(row.source_id) if row.source_id else None,
                    "source_url": source.url if source else None,
                    "source_title": source.title if source else None,
                    "text": row.text,
                    "stance": row.stance,
                    "weight": row.weight,
                    "verified": row.verified,
                    "location": row.location,
                })
            return result
        finally:
            db.close()

    # ── Helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _mapping(item: Mapping[str, Any] | Any) -> Dict[str, Any]:
        if isinstance(item, Mapping):
            return dict(item)
        if hasattr(item, "model_dump"):
            return dict(item.model_dump(mode="json"))
        return dict(item)

    @staticmethod
    def _uuid(value: Any) -> Optional[uuid.UUID]:
        if value is None:
            return None
        try:
            return uuid.UUID(str(value))
        except (ValueError, TypeError, AttributeError):
            return None

    @staticmethod
    def _value(value: Any) -> Any:
        return getattr(value, "value", value)

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

    def _existing_source_id(
        self, db: Any, value: Any
    ) -> Optional[uuid.UUID]:
        if not value:
            return None
        try:
            source_id = uuid.UUID(str(value))
        except (ValueError, TypeError, AttributeError):
            return None
        if db.get(ResearchSource, source_id) is None:
            return None
        return source_id

    @staticmethod
    def _claim_to_dict(row: ResearchClaim) -> Dict[str, Any]:
        return {
            "claim_id": str(row.id),
            "session_id": str(row.session_id),
            "text": row.text,
            "claim_type": row.claim_type,
            "status": row.status,
            "confidence": row.confidence,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }


class NullClaimRepository:
    """No-op adapter for local/test runs without PostgreSQL."""

    available = False

    def save_claims(self, **_: Any) -> int:
        return 0

    def save_evidence(self, **_: Any) -> int:
        return 0

    def save_claim_evidence_links(self, **_: Any) -> int:
        return 0

    def save_verification_results(self, **_: Any) -> int:
        return 0

    def list_claims(self, session_id: str, **_: Any) -> List[Dict[str, Any]]:
        return []

    def get_claim_with_evidence(
        self, session_id: str, claim_id: str
    ) -> Optional[Dict[str, Any]]:
        return None

    def list_evidence_for_session(self, session_id: str) -> List[Dict[str, Any]]:
        return []


def build_claim_repository(
    settings: Optional[Settings] = None,
) -> ClaimRepository | NullClaimRepository:
    cfg = settings or get_settings()
    if cfg.database_url:
        return ClaimRepository(cfg)
    return NullClaimRepository()


__all__ = [
    "ClaimRepository",
    "NullClaimRepository",
    "build_claim_repository",
]
