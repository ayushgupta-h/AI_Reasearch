"""
app/api/memory.py
=================
Research memory API endpoints.

All endpoints are owner-scoped: callers can only create, read, update or
delete memory records that belong to them (and optionally their project /
research session).
"""

from __future__ import annotations

from typing import Any, List, Optional

from fastapi import APIRouter, Depends, Path, Query

from app.errors import AuthenticationError, NotFoundError
from app.schemas.errors import ApiError
from app.security.dependencies import get_current_user
from app.services.container import get_memory_service
from app.services.research_memory_service import MemoryRecord, ResearchMemoryService

router = APIRouter(tags=["Research Memory"])

MemoryIdPath = Path(
    ...,
    pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    description="UUID memory id.",
)


def _owner(current_user: dict) -> str:
    owner = str(current_user.get("user_id") or "").strip()
    if not owner:
        raise AuthenticationError("Authentication required.")
    return owner


@router.get(
    "/memory/search",
    response_model=dict,
    responses={
        401: {"model": ApiError, "description": "Authentication required."},
    },
    summary="Semantically search research memory",
)
async def search_memory(
    q: Optional[str] = Query(None, description="Search query"),
    query: Optional[str] = Query(None, description="Search query alias"),
    k: int = Query(6, ge=1, le=20, description="Max results"),
    project_id: Optional[str] = Query(None, description="Filter by project"),
    session_id: Optional[str] = Query(None, description="Filter by session"),
    content_type: Optional[str] = Query(None, description="Filter by content type"),
    verified_only: bool = Query(False, description="Only return verified findings"),
    current_user: dict = Depends(get_current_user),
    service: ResearchMemoryService = Depends(get_memory_service),
) -> dict:
    """Return memory records semantically relevant to *q* or *query*, owner-scoped."""
    user_id = _owner(current_user)
    search_text = (q or query or "").strip()
    if not search_text:
        records = service.list_memories(
            user_id=user_id,
            project_id=project_id,
            session_id=session_id,
            content_type=content_type,
            limit=k,
        )
        return {
            "query": "",
            "user_id": user_id,
            "results": [r.__dict__ for r in records],
            "total": len(records),
        }

    results = service.retrieve_memories(
        user_id=user_id,
        query=search_text,
        k=k,
        project_id=project_id,
        session_id=session_id,
        content_type=content_type,
        verified_only=verified_only,
    )
    return {
        "query": search_text,
        "user_id": user_id,
        "results": [r.__dict__ for r in results],
        "total": len(results),
    }


@router.get(
    "/memory",
    response_model=dict,
    responses={
        401: {"model": ApiError, "description": "Authentication required."},
    },
    summary="List research memory records",
)
async def list_memory(
    project_id: Optional[str] = Query(None),
    session_id: Optional[str] = Query(None),
    verification_status: Optional[str] = Query(None),
    content_type: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    current_user: dict = Depends(get_current_user),
    service: ResearchMemoryService = Depends(get_memory_service),
) -> dict:
    """List owned memory records, newest first."""
    user_id = _owner(current_user)
    records = service.list_memories(
        user_id=user_id,
        project_id=project_id,
        session_id=session_id,
        verification_status=verification_status,
        content_type=content_type,
        limit=limit,
        offset=offset,
    )
    return {
        "user_id": user_id,
        "results": [r.__dict__ for r in records],
        "total": len(records),
        "limit": limit,
        "offset": offset,
    }


@router.post(
    "/memory",
    response_model=dict,
    responses={
        401: {"model": ApiError, "description": "Authentication required."},
    },
    summary="Create a research memory record",
)
async def create_memory(
    content: str,
    content_type: str = "finding",
    verification_status: str = "unverified",
    confidence: Optional[float] = None,
    project_id: Optional[str] = None,
    session_id: Optional[str] = None,
    source_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
    service: ResearchMemoryService = Depends(get_memory_service),
) -> dict:
    """Persist one memory record (PostgreSQL + ChromaDB)."""
    user_id = _owner(current_user)
    record = service.store_memory(
        user_id=user_id,
        content=content,
        content_type=content_type,
        verification_status=verification_status,
        confidence=confidence,
        project_id=project_id,
        session_id=session_id,
        source_id=source_id,
    )
    if record is None:
        return {
            "user_id": user_id,
            "created": False,
            "reason": "duplicate or persistence unavailable",
        }
    return {
        "user_id": user_id,
        "created": True,
        "record": record.__dict__,
    }


@router.get(
    "/memory/{memory_id}",
    response_model=dict,
    responses={
        401: {"model": ApiError, "description": "Authentication required."},
        404: {"model": ApiError, "description": "Memory not found or not owned."},
    },
    summary="Get one memory record",
)
async def get_memory(
    memory_id: str = MemoryIdPath,
    current_user: dict = Depends(get_current_user),
    service: ResearchMemoryService = Depends(get_memory_service),
) -> dict:
    """Return one owned memory record."""
    user_id = _owner(current_user)
    record = service.get_memory(memory_id=memory_id, user_id=user_id)
    if record is None:
        raise NotFoundError("Memory not found.")
    return {"record": record.__dict__}


@router.put(
    "/memory/{memory_id}",
    response_model=dict,
    responses={
        401: {"model": ApiError, "description": "Authentication required."},
        404: {"model": ApiError, "description": "Memory not found or not owned."},
    },
    summary="Update a memory record",
)
async def update_memory(
    memory_id: str = MemoryIdPath,
    content: Optional[str] = None,
    verification_status: Optional[str] = None,
    confidence: Optional[float] = None,
    current_user: dict = Depends(get_current_user),
    service: ResearchMemoryService = Depends(get_memory_service),
) -> dict:
    """Update an owned memory record (verified findings cannot be overwritten)."""
    user_id = _owner(current_user)
    record = service.update_memory(
        memory_id=memory_id,
        user_id=user_id,
        content=content,
        verification_status=verification_status,
        confidence=confidence,
    )
    if record is None:
        raise NotFoundError("Memory not found or update not permitted.")
    return {"record": record.__dict__}


@router.delete(
    "/memory/{memory_id}",
    response_model=dict,
    responses={
        401: {"model": ApiError, "description": "Authentication required."},
        404: {"model": ApiError, "description": "Memory not found or not owned."},
    },
    summary="Delete a memory record",
)
async def delete_memory(
    memory_id: str = MemoryIdPath,
    current_user: dict = Depends(get_current_user),
    service: ResearchMemoryService = Depends(get_memory_service),
) -> dict:
    """Soft-delete an owned memory record."""
    user_id = _owner(current_user)
    ok = service.delete_memory(memory_id=memory_id, user_id=user_id)
    if not ok:
        raise NotFoundError("Memory not found or not owned.")
    return {"deleted": True, "memory_id": memory_id}


__all__ = ["router"]
