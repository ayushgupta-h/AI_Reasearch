"""
app/repositories/sources.py
===========================
Persistence adapter for search sources.

Every source row is bound to a research session (``session_id``) which in turn
carries an ``owner_id``. Ownership is therefore enforced transitively: a caller
can only touch sources belonging to sessions they own. Missing metadata is
never fabricated — optional fields are stored as ``NULL``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence

from sqlalchemy import and_, select

from app.config import Settings, get_settings
from app.database.models.domain import ResearchSession
from app.database.models.research import ResearchSource
from app.database.session import get_session_factory
from app.observability import get_logger

logger = get_logger("app.repositories.sources")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _uuid(value: Any) -> Optional[uuid.UUID]:
    if value is None:
        return None
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


class SourceRepository:
    """PostgreSQL-backed CRUD for research sources, owner-scoped via session."""

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

    # ── Verify ownership (transitive: source -> session -> owner) ──────

    def _session_owner(
        self, session, session_id: uuid.UUID
    ) -> Optional[uuid.UUID]:
        row = session.get(ResearchSession, session_id)
        if row is None or row.deleted:
            return None
        return row.owner_id

    # ── Create ─────────────────────────────────────────────────────────

    def create_source(
        self,
        *,
        session_id: str,
        owner_id: str,
        url: Optional[str],
        title: str,
        source_type: str,
        author: Optional[str] = None,
        publisher: Optional[str] = None,
        publication_date: Optional[str] = None,
        snippet: Optional[str] = None,
        citation_status: str = "unverified",
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> Optional[dict[str, Any]]:
        """Insert one source row, enforcing session ownership.

        Returns the persisted row dict, or ``None`` if the session does not
        exist or is not owned by *owner_id*.
        """
        session_uuid = _uuid(session_id)
        owner_uuid = _uuid(owner_id)
        if session_uuid is None or owner_uuid is None:
            return None

        published_dt: Optional[datetime] = None
        if publication_date:
            try:
                published_dt = datetime.fromisoformat(publication_date)
            except ValueError:
                published_dt = None

        db = self._factory()()
        try:
            if self._session_owner(db, session_uuid) != owner_uuid:
                return None

            row = ResearchSource(
                session_id=session_uuid,
                title=title[:500],
                url=url,
                source_type=source_type[:30],
                snippet=snippet,
                author=author[:200] if author else None,
                publisher=publisher[:200] if publisher else None,
                published_date=published_dt,
                accessed_at=_utcnow(),
                citation_status=citation_status[:20],
                metadata_json=(
                    str(metadata) if metadata is not None else None
                ),
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            return self._row_to_dict(row)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    # ── Read ───────────────────────────────────────────────────────────

    def get_source(
        self, *, source_id: str, owner_id: str
    ) -> Optional[dict[str, Any]]:
        """Return one source row, only if owned by *owner_id*."""
        source_uuid = _uuid(source_id)
        owner_uuid = _uuid(owner_id)
        if source_uuid is None or owner_uuid is None:
            return None

        db = self._factory()()
        try:
            row = db.get(ResearchSource, source_uuid)
            if row is None or row.deleted:
                return None
            owner = self._session_owner(db, row.session_id)
            if owner != owner_uuid:
                return None
            return self._row_to_dict(row)
        finally:
            db.close()

    def list_sources(
        self,
        *,
        owner_id: str,
        session_id: Optional[str] = None,
        source_type: Optional[str] = None,
        publisher: Optional[str] = None,
        citation_status: Optional[str] = None,
        created_after: Optional[datetime] = None,
        created_before: Optional[datetime] = None,
        sort: str = "created_at",
        order: str = "desc",
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """List sources owned by *owner_id*, optionally scoped to one session.

        Filters are combined with AND. Missing metadata is never fabricated —
        absent values simply do not match.
        """
        owner_uuid = _uuid(owner_id)
        if owner_uuid is None:
            return [], 0

        session_uuid = _uuid(session_id) if session_id else None

        db = self._factory()()
        try:
            # Ownership gate: if a session is pinned, verify the caller owns it.
            if session_uuid is not None:
                if self._session_owner(db, session_uuid) != owner_uuid:
                    return [], 0

            stmt = select(ResearchSource).where(
                and_(
                    ResearchSource.deleted.is_(False),
                    ResearchSource.session_id == session_uuid
                    if session_uuid is not None
                    else ResearchSource.session_id.in_(
                        select(ResearchSession.id).where(
                            and_(
                                ResearchSession.owner_id == owner_uuid,
                                ResearchSession.deleted.is_(False),
                            )
                        )
                    ),
                )
            )

            if source_type:
                stmt = stmt.where(ResearchSource.source_type == source_type)
            if publisher:
                stmt = stmt.where(ResearchSource.publisher == publisher)
            if citation_status:
                stmt = stmt.where(
                    ResearchSource.citation_status == citation_status
                )
            if created_after is not None:
                stmt = stmt.where(ResearchSource.created_at >= created_after)
            if created_before is not None:
                stmt = stmt.where(ResearchSource.created_at <= created_before)

            column = getattr(ResearchSource, sort, ResearchSource.created_at)
            direction = (
                column.asc() if order.lower() == "asc" else column.desc()
            )
            stmt = stmt.order_by(direction, ResearchSource.id.desc())

            total_stmt = select(
                ResearchSource.id
            ).where(stmt.whereclause)  # type: ignore[arg-type]
            total = len(db.scalars(total_stmt).all())

            rows = db.scalars(
                stmt.limit(limit).offset(offset)
            ).all()

            return [self._row_to_dict(row) for row in rows], total
        finally:
            db.close()

    # ── Update ─────────────────────────────────────────────────────────

    def update_source_status(
        self,
        *,
        source_id: str,
        owner_id: str,
        citation_status: str,
    ) -> Optional[dict[str, Any]]:
        """Update a source's citation validation status (owner-scoped)."""
        source_uuid = _uuid(source_id)
        owner_uuid = _uuid(owner_id)
        if source_uuid is None or owner_uuid is None:
            return None

        db = self._factory()()
        try:
            row = db.get(ResearchSource, source_uuid)
            if row is None or row.deleted:
                return None
            if self._session_owner(db, row.session_id) != owner_uuid:
                return None

            row.citation_status = citation_status[:20]
            row.updated_at = _utcnow()
            db.commit()
            db.refresh(row)
            return self._row_to_dict(row)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    # ── Delete ─────────────────────────────────────────────────────────

    def delete_source(self, *, source_id: str, owner_id: str) -> bool:
        """Soft-delete one owned source. Returns ``True`` if deleted."""
        source_uuid = _uuid(source_id)
        owner_uuid = _uuid(owner_id)
        if source_uuid is None or owner_uuid is None:
            return False

        db = self._factory()()
        try:
            row = db.get(ResearchSource, source_uuid)
            if row is None or row.deleted:
                return False
            if self._session_owner(db, row.session_id) != owner_uuid:
                return False

            row.deleted = True
            row.deleted_at = _utcnow()
            db.commit()
            return True
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    # ── Statistics ─────────────────────────────────────────────────────

    def statistics(
        self,
        *,
        owner_id: str,
        session_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Aggregate source counts by type, publisher and citation status."""
        owner_uuid = _uuid(owner_id)
        if owner_uuid is None:
            return {
                "total": 0,
                "by_source_type": {},
                "by_publisher": {},
                "by_citation_status": {},
            }

        session_uuid = _uuid(session_id) if session_id else None

        db = self._factory()()
        try:
            # Ownership gate
            if session_uuid is not None:
                if self._session_owner(db, session_uuid) != owner_uuid:
                    return {
                        "total": 0,
                        "by_source_type": {},
                        "by_publisher": {},
                        "by_citation_status": {},
                    }

            base = select(ResearchSource).where(
                and_(
                    ResearchSource.deleted.is_(False),
                    ResearchSource.session_id == session_uuid
                    if session_uuid is not None
                    else ResearchSource.session_id.in_(
                        select(ResearchSession.id).where(
                            and_(
                                ResearchSession.owner_id == owner_uuid,
                                ResearchSession.deleted.is_(False),
                            )
                        )
                    ),
                )
            )

            rows = db.scalars(base).all()

            by_type: dict[str, int] = {}
            by_publisher: dict[str, int] = {}
            by_status: dict[str, int] = {}
            for row in rows:
                by_type[row.source_type] = by_type.get(row.source_type, 0) + 1
                pub = row.publisher or "unknown"
                by_publisher[pub] = by_publisher.get(pub, 0) + 1
                by_status[row.citation_status] = (
                    by_status.get(row.citation_status, 0) + 1
                )

            return {
                "total": len(rows),
                "by_source_type": by_type,
                "by_publisher": by_publisher,
                "by_citation_status": by_status,
            }
        finally:
            db.close()

    # ── Helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _row_to_dict(row: ResearchSource) -> dict[str, Any]:
        return {
            "source_id": str(row.id),
            "session_id": str(row.session_id),
            "url": row.url,
            "title": row.title,
            "author": row.author,
            "publisher": row.publisher,
            "publication_date": (
                row.published_date.isoformat() if row.published_date else None
            ),
            "source_type": row.source_type,
            "snippet": row.snippet,
            "retrieval_date": (
                row.accessed_at.isoformat() if row.accessed_at else None
            ),
            "relevance": None,  # populated by the search pipeline, not stored
            "credibility_evaluation_reference": None,
            "citation_validation_status": row.citation_status,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            "deleted": row.deleted,
        }


class NullSourceRepository:
    """No-op adapter for local/test runs without PostgreSQL."""

    available = False

    def create_source(self, **_: Any) -> Optional[dict[str, Any]]:
        return None

    def get_source(self, **_: Any) -> Optional[dict[str, Any]]:
        return None

    def list_sources(self, **_: Any) -> tuple[list[dict[str, Any]], int]:
        return [], 0

    def update_source_status(self, **_: Any) -> Optional[dict[str, Any]]:
        return None

    def delete_source(self, **_: Any) -> bool:
        return False

    def statistics(self, **_: Any) -> dict[str, Any]:
        return {
            "total": 0,
            "by_source_type": {},
            "by_publisher": {},
            "by_citation_status": {},
        }


def build_source_repository(
    settings: Optional[Settings] = None,
) -> SourceRepository | NullSourceRepository:
    cfg = settings or get_settings()
    if cfg.database_url:
        return SourceRepository(cfg)
    return NullSourceRepository()


__all__ = [
    "SourceRepository",
    "NullSourceRepository",
    "build_source_repository",
]
