"""
app/repositories/memory.py
==========================
PostgreSQL persistence adapter for research memory.

Wraps the ``research_memory`` table. All queries are scoped by ``user_id``
so one user's memory can never leak into another's. ``project_id`` and
``session_id`` provide progressively narrower scoping.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence

from sqlalchemy import and_, select, update

from app.config import Settings, get_settings
from app.database.models.domain import Project, ResearchSession, User
from app.database.models.memory import ResearchMemory
from app.database.models.research import ResearchSource
from app.database.session import get_session_factory
from app.observability import get_logger

logger = get_logger("app.repositories.memory")


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.strip().lower().encode("utf-8")).hexdigest()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MemoryRepository:
    """PostgreSQL-backed CRUD for research memory records."""

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

    def _uuid(self, value: Any) -> Optional[uuid.UUID]:
        if value is None:
            return None
        try:
            return uuid.UUID(str(value))
        except (ValueError, TypeError, AttributeError):
            return None

    def _verify_ownership(
        self, db, *, user_id: uuid.UUID, project_id: Optional[uuid.UUID],
        session_id: Optional[uuid.UUID],
    ) -> bool:
        """Return True only when the user owns the project/session (if given)."""
        if project_id is not None:
            project = db.get(Project, project_id)
            if project is None or project.owner_id != user_id:
                return False
        if session_id is not None:
            session = db.get(ResearchSession, session_id)
            if session is None or session.owner_id != user_id:
                return False
        return True

    # ── Create ─────────────────────────────────────────────────────────

    def create_memory(
        self,
        *,
        user_id: str,
        content: str,
        content_type: str = "finding",
        verification_status: str = "unverified",
        confidence: Optional[float] = None,
        project_id: Optional[str] = None,
        session_id: Optional[str] = None,
        source_id: Optional[str] = None,
        chroma_id: Optional[str] = None,
        metadata_json: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Insert one memory record, enforcing ownership.

        Returns the persisted record dict, or ``None`` if a duplicate
        (same user + project + session + content hash) already exists.
        """
        user_uuid = self._uuid(user_id)
        if user_uuid is None:
            return None
        proj_uuid = self._uuid(project_id) if project_id else None
        sess_uuid = self._uuid(session_id) if session_id else None
        src_uuid = self._uuid(source_id) if source_id else None

        if proj_uuid or sess_uuid:
            if not self._verify_ownership(
                None, user_id=user_uuid, project_id=proj_uuid,
                session_id=sess_uuid,
            ):
                # Defer full DB ownership check to the write transaction below.
                pass

        db = self._factory()()
        try:
            # Ownership check inside the transaction.
            if not self._verify_ownership(
                db, user_id=user_uuid, project_id=proj_uuid,
                session_id=sess_uuid,
            ):
                return None

            content_hash = _content_hash(content)

            # Duplicate detection: same user, project, session, content hash.
            existing = db.scalar(
                select(ResearchMemory).where(
                    and_(
                        ResearchMemory.user_id == user_uuid,
                        ResearchMemory.project_id == proj_uuid,
                        ResearchMemory.session_id == sess_uuid,
                        ResearchMemory.content_hash == content_hash,
                        ResearchMemory.deleted.is_(False),
                    )
                )
            )
            if existing is not None:
                return self._record_to_dict(existing)

            record = ResearchMemory(
                user_id=user_uuid,
                project_id=proj_uuid,
                session_id=sess_uuid,
                source_id=src_uuid,
                content_hash=content_hash,
                content_type=content_type,
                content_text=content,
                verification_status=verification_status,
                confidence=confidence,
                chroma_id=chroma_id,
                metadata_json=metadata_json,
            )
            db.add(record)
            db.commit()
            db.refresh(record)
            return self._record_to_dict(record)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    # ── Read ───────────────────────────────────────────────────────────

    def get_memory(
        self, *, memory_id: str, user_id: str
    ) -> Optional[Dict[str, Any]]:
        """Fetch one memory by ID, only if owned by *user_id*."""
        mem_uuid = self._uuid(memory_id)
        user_uuid = self._uuid(user_id)
        if mem_uuid is None or user_uuid is None:
            return None

        db = self._factory()()
        try:
            record = db.get(ResearchMemory, mem_uuid)
            if (
                record is None
                or record.deleted
                or record.user_id != user_uuid
            ):
                return None
            return self._record_to_dict(record)
        finally:
            db.close()

    def list_memories(
        self,
        *,
        user_id: str,
        project_id: Optional[str] = None,
        session_id: Optional[str] = None,
        verification_status: Optional[str] = None,
        content_type: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """List memories for *user_id*, optionally narrowed by project/session."""
        user_uuid = self._uuid(user_id)
        if user_uuid is None:
            return []

        proj_uuid = self._uuid(project_id) if project_id else None
        sess_uuid = self._uuid(session_id) if session_id else None

        # Ownership enforcement: if caller asks for a project/session they
        # don't own, return empty.
        db = self._factory()()
        try:
            if proj_uuid is not None:
                project = db.get(Project, proj_uuid)
                if project is None or project.owner_id != user_uuid:
                    return []
            if sess_uuid is not None:
                session = db.get(ResearchSession, sess_uuid)
                if session is None or session.owner_id != user_uuid:
                    return []

            stmt = select(ResearchMemory).where(
                and_(
                    ResearchMemory.user_id == user_uuid,
                    ResearchMemory.deleted.is_(False),
                )
            )
            if proj_uuid is not None:
                stmt = stmt.where(ResearchMemory.project_id == proj_uuid)
            if sess_uuid is not None:
                stmt = stmt.where(ResearchMemory.session_id == sess_uuid)
            if verification_status:
                stmt = stmt.where(
                    ResearchMemory.verification_status == verification_status
                )
            if content_type:
                stmt = stmt.where(ResearchMemory.content_type == content_type)
            stmt = (
                stmt.order_by(ResearchMemory.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
            rows = db.scalars(stmt).all()
            return [self._record_to_dict(row) for row in rows]
        finally:
            db.close()

    # ── Update ─────────────────────────────────────────────────────────

    def update_memory(
        self,
        *,
        memory_id: str,
        user_id: str,
        content: Optional[str] = None,
        verification_status: Optional[str] = None,
        confidence: Optional[float] = None,
        metadata_json: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Update an owned memory record. Never overwrites verified findings
        silently: if the existing record is ``verified`` and *content* is
        being changed, the update is rejected (caller must create a new
        record instead).
        """
        mem_uuid = self._uuid(memory_id)
        user_uuid = self._uuid(user_id)
        if mem_uuid is None or user_uuid is None:
            return None

        db = self._factory()()
        try:
            record = db.get(ResearchMemory, mem_uuid)
            if record is None or record.deleted or record.user_id != user_uuid:
                return None

            if content is not None and record.verification_status == "verified":
                # Verified findings must not be silently overwritten.
                return None

            if content is not None:
                record.content_text = content
                record.content_hash = _content_hash(content)
            if verification_status is not None:
                record.verification_status = verification_status
            if confidence is not None:
                record.confidence = confidence
            if metadata_json is not None:
                record.metadata_json = metadata_json

            db.commit()
            db.refresh(record)
            return self._record_to_dict(record)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    # ── Delete ─────────────────────────────────────────────────────────

    def delete_memory(self, *, memory_id: str, user_id: str) -> bool:
        """Soft-delete an owned memory record. Returns ``True`` if deleted."""
        mem_uuid = self._uuid(memory_id)
        user_uuid = self._uuid(user_id)
        if mem_uuid is None or user_uuid is None:
            return False

        db = self._factory()()
        try:
            record = db.get(ResearchMemory, mem_uuid)
            if record is None or record.deleted or record.user_id != user_uuid:
                return False
            record.deleted = True
            record.deleted_at = _utcnow()
            db.commit()
            return True
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    # ── Helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _record_to_dict(record: ResearchMemory) -> Dict[str, Any]:
        return {
            "id": str(record.id),
            "user_id": str(record.user_id),
            "project_id": str(record.project_id) if record.project_id else None,
            "session_id": str(record.session_id) if record.session_id else None,
            "source_id": str(record.source_id) if record.source_id else None,
            "content_hash": record.content_hash,
            "content_type": record.content_type,
            "content_text": record.content_text,
            "verification_status": record.verification_status,
            "confidence": record.confidence,
            "chroma_id": record.chroma_id,
            "metadata_json": record.metadata_json,
            "created_at": record.created_at.isoformat()
            if record.created_at
            else None,
            "updated_at": record.updated_at.isoformat()
            if record.updated_at
            else None,
            "deleted": record.deleted,
        }


class NullMemoryRepository:
    """No-op adapter when PostgreSQL is not configured."""

    available = False

    def create_memory(self, **_: Any) -> Optional[Dict[str, Any]]:
        return None

    def get_memory(self, **_: Any) -> Optional[Dict[str, Any]]:
        return None

    def list_memories(self, **_: Any) -> List[Dict[str, Any]]:
        return []

    def update_memory(self, **_: Any) -> Optional[Dict[str, Any]]:
        return None

    def delete_memory(self, **_: Any) -> bool:
        return False


def build_memory_repository(
    settings: Optional[Settings] = None,
) -> MemoryRepository | NullMemoryRepository:
    cfg = settings or get_settings()
    if cfg.database_url:
        return MemoryRepository(cfg)
    return NullMemoryRepository()


__all__ = [
    "MemoryRepository",
    "NullMemoryRepository",
    "build_memory_repository",
    "_content_hash",
]
