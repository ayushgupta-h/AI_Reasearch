"""
app/services/source_service.py
==============================
Search source management service.

Wraps :class:`app.repositories.sources.SourceRepository` and adds the
business rules for the source management API:

* Sources are created from :class:`search_sources.SearchResult` instances
  (or raw dicts) and linked to a research session.
* Ownership is enforced transitively through the session's ``owner_id``.
* Missing metadata is never fabricated — optional fields stay ``None``.
* The ``PATCH /sources/{source_id}/status`` endpoint updates only the
  citation validation status.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from app.config import Settings, get_settings
from app.errors import AuthenticationError, NotFoundError
from app.observability import get_logger
from app.repositories.sources import (
    NullSourceRepository,
    build_source_repository,
)

logger = get_logger("app.services.sources")


def _require_owner(owner_id: Optional[str]) -> str:
    owner = (owner_id or "").strip()
    if not owner:
        raise AuthenticationError("Authentication required.")
    return owner


class SourceService:
    """Business logic for source management, owner-scoped via session."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self._settings = settings or get_settings()
        self._repo = build_source_repository(self._settings)

    @property
    def available(self) -> bool:
        return self._repo.available

    # ── Create ─────────────────────────────────────────────────────────

    def create_source(
        self,
        *,
        owner_id: str,
        session_id: str,
        url: Optional[str],
        title: str,
        source_type: str = "web",
        author: Optional[str] = None,
        publisher: Optional[str] = None,
        publication_date: Optional[str] = None,
        snippet: Optional[str] = None,
        citation_status: str = "unverified",
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> Optional[dict[str, Any]]:
        """Persist one source row, enforcing session ownership.

        Returns the persisted dict, or ``None`` when the caller does not own
        the session or PostgreSQL is unavailable.
        """
        _require_owner(owner_id)
        return self._repo.create_source(
            session_id=session_id,
            owner_id=owner_id,
            url=url,
            title=title,
            source_type=source_type,
            author=author,
            publisher=publisher,
            publication_date=publication_date,
            snippet=snippet,
            citation_status=citation_status,
            metadata=metadata,
        )

    def create_from_search_result(
        self,
        *,
        owner_id: str,
        session_id: str,
        result: Mapping[str, Any],
        citation_status: str = "unverified",
    ) -> Optional[dict[str, Any]]:
        """Build a source row from a :class:`search_sources.SearchResult`-like
        mapping, preserving only the fields the provider actually supplied.
        """
        _require_owner(owner_id)
        metadata: dict[str, Any] = {}
        for key in (
            "source_name",
            "authors",
            "doi",
            "venue",
            "methodology",
            "peer_reviewed",
            "references_available",
            "source_nature",
            "metadata",
        ):
            value = result.get(key)
            if value is not None:
                metadata[key] = value

        return self._repo.create_source(
            session_id=session_id,
            owner_id=owner_id,
            url=result.get("url"),
            title=result.get("title", "Untitled"),
            source_type=result.get("source_type", "web"),
            author=result.get("author") or (
                ", ".join(result.get("authors", [])) if result.get("authors")
                else None
            ),
            publisher=result.get("publisher"),
            publication_date=result.get("publication_date"),
            snippet=result.get("snippet"),
            citation_status=citation_status,
            metadata=metadata or None,
        )

    # ── Read ───────────────────────────────────────────────────────────

    def get_source(
        self, *, source_id: str, owner_id: str
    ) -> Optional[dict[str, Any]]:
        """Return one source row if owned by *owner_id*."""
        _require_owner(owner_id)
        row = self._repo.get_source(source_id=source_id, owner_id=owner_id)
        if row is None:
            raise NotFoundError("Source not found.")
        return row

    def list_sources(
        self,
        *,
        owner_id: str,
        session_id: Optional[str] = None,
        source_type: Optional[str] = None,
        publisher: Optional[str] = None,
        citation_status: Optional[str] = None,
        created_after: Optional[str] = None,
        created_before: Optional[str] = None,
        sort: str = "created_at",
        order: str = "desc",
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """List owned sources with optional filters."""
        _require_owner(owner_id)
        from datetime import datetime

        created_after_dt = None
        created_before_dt = None
        if created_after:
            try:
                created_after_dt = datetime.fromisoformat(created_after)
            except ValueError:
                pass
        if created_before:
            try:
                created_before_dt = datetime.fromisoformat(created_before)
            except ValueError:
                pass

        return self._repo.list_sources(
            owner_id=owner_id,
            session_id=session_id,
            source_type=source_type,
            publisher=publisher,
            citation_status=citation_status,
            created_after=created_after_dt,
            created_before=created_before_dt,
            sort=sort,
            order=order,
            limit=limit,
            offset=offset,
        )

    # ── Update ─────────────────────────────────────────────────────────

    def update_source_status(
        self,
        *,
        source_id: str,
        owner_id: str,
        citation_status: str,
    ) -> dict[str, Any]:
        """Update citation validation status for an owned source."""
        _require_owner(owner_id)
        row = self._repo.update_source_status(
            source_id=source_id,
            owner_id=owner_id,
            citation_status=citation_status,
        )
        if row is None:
            raise NotFoundError("Source not found.")
        return row

    # ── Delete ─────────────────────────────────────────────────────────

    def delete_source(self, *, source_id: str, owner_id: str) -> None:
        """Soft-delete an owned source."""
        _require_owner(owner_id)
        ok = self._repo.delete_source(source_id=source_id, owner_id=owner_id)
        if not ok:
            raise NotFoundError("Source not found.")

    # ── Statistics ─────────────────────────────────────────────────────

    def statistics(
        self,
        *,
        owner_id: str,
        session_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Aggregate source counts by type, publisher and citation status."""
        _require_owner(owner_id)
        return self._repo.statistics(
            owner_id=owner_id, session_id=session_id
        )


__all__ = ["SourceService"]
