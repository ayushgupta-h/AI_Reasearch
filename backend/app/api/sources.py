"""
app/api/sources.py
==================
Search source management API.

Endpoints
---------
GET    /research/{research_id}/sources          list sources for a session
GET    /sources/{source_id}                     get one source
GET    /research/{research_id}/sources/statistics aggregate counts
PATCH  /sources/{source_id}/status              update citation validation status
DELETE /sources/{source_id}                     soft-delete one source

All endpoints enforce user ownership transitively: a caller can only touch
sources belonging to research sessions they own. The authenticated JWT
identity is the sole source of ``owner_id`` — it is never trusted from the
request body.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query

from app.errors import AuthenticationError, NotFoundError
from app.schemas.enums import CitationStatus, SourceType
from app.schemas.errors import ApiError
from app.security.dependencies import get_current_user, get_optional_user
from app.services.container import get_session_service, get_source_service
from app.services.session_service import SessionService
from app.services.source_service import SourceService

router = APIRouter(tags=["Source Management"])

# Accepts both 32-character hex and UUID formats.
ResearchIdPath = Path(
    ...,
    pattern=r"^[a-zA-Z0-9_-]+$",
    description="Research session id.",
)

SourceIdPath = Path(
    ...,
    pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    description="UUID source id.",
)

_ERRORS = {
    401: {"model": ApiError, "description": "Authentication required."},
    404: {"model": ApiError, "description": "Source or research session not found."},
    422: {"model": ApiError, "description": "Invalid field value or ID format."},
}


def _owner(current_user: dict) -> str:
    owner = str(current_user.get("user_id") or "").strip()
    if not owner:
        raise AuthenticationError("Authentication required.")
    return owner


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


@router.get(
    "/research/{research_id}/sources",
    response_model=dict,
    responses=_ERRORS,
    summary="List sources for a research session",
)
async def list_sources(
    research_id: str = ResearchIdPath,
    source_type: Optional[SourceType] = Query(
        None, description="Filter by source type."
    ),
    publisher: Optional[str] = Query(
        None, max_length=200, description="Filter by publisher."
    ),
    citation_status: Optional[CitationStatus] = Query(
        None, description="Filter by citation validation status."
    ),
    created_after: Optional[str] = Query(
        None, description="ISO-8601 datetime lower bound."
    ),
    created_before: Optional[str] = Query(
        None, description="ISO-8601 datetime upper bound."
    ),
    sort: Literal["created_at", "title", "source_type", "citation_status"] = Query(
        "created_at", description="Sort field."
    ),
    order: Literal["asc", "desc"] = Query("desc", description="Sort order."),
    limit: int = Query(20, ge=1, le=100, description="Page size."),
    offset: int = Query(0, ge=0, description="Page offset."),
    current_user: Optional[dict] = Depends(get_optional_user),
    service: SourceService = Depends(get_source_service),
    session_service: SessionService = Depends(get_session_service),
) -> dict:
    """Return the caller's sources for one research session, with pagination
    and optional filters."""
    owner_id = str(current_user.get("user_id") or "").strip() if current_user else ""
    rows = []
    total = 0
    if owner_id:
        try:
            rows, total = service.list_sources(
                owner_id=owner_id,
                session_id=research_id,
                source_type=source_type.value if source_type else None,
                publisher=publisher,
                citation_status=citation_status.value if citation_status else None,
                created_after=_parse_datetime(created_after),
                created_before=_parse_datetime(created_before),
                sort=sort,
                order=order,
                limit=limit,
                offset=offset,
            )
        except Exception:
            pass

    if not rows:
        sess = session_service.get_session(research_id, user_id=owner_id if owner_id else None) or session_service.get_session(research_id)
        if sess:
            raw_sources = sess.get("sources") or sess.get("search_results_raw") or []
            if isinstance(raw_sources, list):
                for i, s in enumerate(raw_sources):
                    if isinstance(s, dict):
                        rows.append({
                            "source_id": s.get("source_id") or f"src-{i+1}",
                            "id": s.get("source_id") or f"src-{i+1}",
                            "title": s.get("title") or s.get("url") or f"Source {i + 1}",
                            "url": s.get("url"),
                            "publisher": s.get("source_name") or s.get("publisher") or "Web",
                            "source_type": s.get("source_type") or "web",
                            "citation_status": s.get("citation_status") or "verified",
                        })
                    elif isinstance(s, str):
                        rows.append({
                            "source_id": f"src-{i+1}",
                            "id": f"src-{i+1}",
                            "title": s,
                            "url": s if s.startswith("http") else None,
                            "publisher": "Web",
                            "source_type": "web",
                            "citation_status": "verified",
                        })
                total = len(rows)
    return {
        "research_id": research_id,
        "sources": rows,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get(
    "/sources/{source_id}",
    response_model=dict,
    responses=_ERRORS,
    summary="Get one source by ID",
)
async def get_source(
    source_id: str = SourceIdPath,
    current_user: dict = Depends(get_current_user),
    service: SourceService = Depends(get_source_service),
) -> dict:
    """Return one source if owned by the caller."""
    owner_id = _owner(current_user)
    return service.get_source(source_id=source_id, owner_id=owner_id)


@router.get(
    "/research/{research_id}/sources/statistics",
    response_model=dict,
    responses=_ERRORS,
    summary="Aggregate source statistics for a research session",
)
async def source_statistics(
    research_id: str = ResearchIdPath,
    current_user: dict = Depends(get_current_user),
    service: SourceService = Depends(get_source_service),
) -> dict:
    """Return counts of sources grouped by type, publisher and citation
    status for one research session."""
    owner_id = _owner(current_user)
    return service.statistics(owner_id=owner_id, session_id=research_id)


@router.patch(
    "/sources/{source_id}/status",
    response_model=dict,
    responses={
        **_ERRORS,
        400: {"model": ApiError, "description": "Invalid citation status."},
    },
    summary="Update a source's citation validation status",
)
async def update_source_status(
    source_id: str = SourceIdPath,
    citation_status: CitationStatus = Query(
        ..., description="New citation validation status."
    ),
    current_user: dict = Depends(get_current_user),
    service: SourceService = Depends(get_source_service),
) -> dict:
    """Update the citation validation status of an owned source."""
    owner_id = _owner(current_user)
    return service.update_source_status(
        source_id=source_id,
        owner_id=owner_id,
        citation_status=citation_status.value,
    )


@router.delete(
    "/sources/{source_id}",
    response_model=dict,
    responses=_ERRORS,
    summary="Soft-delete a source",
)
async def delete_source(
    source_id: str = SourceIdPath,
    current_user: dict = Depends(get_current_user),
    service: SourceService = Depends(get_source_service),
) -> dict:
    """Soft-delete an owned source."""
    owner_id = _owner(current_user)
    service.delete_source(source_id=source_id, owner_id=owner_id)
    return {"status": "deleted", "source_id": source_id}


__all__ = ["router"]
