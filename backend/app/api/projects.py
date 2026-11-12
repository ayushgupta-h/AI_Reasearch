"""
app/api/projects.py
===================
Project CRUD and nested research-session creation/listing.

Every route requires a verified JWT and derives ``owner_id`` from it. Request
bodies never carry ownership.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.errors import AuthenticationError
from app.schemas.enums import LLMProvider, ProjectStatus, SessionStatus
from app.schemas.errors import ApiError
from app.schemas.requests import (
    ProjectCreateRequest,
    ProjectUpdateRequest,
    ResearchCreateRequest,
)
from app.schemas.responses import (
    DeletedProjectResponse,
    ProjectRecord,
    ProjectsResponse,
    ResearchListResponse,
    ResearchRecord,
)
from app.security.dependencies import get_current_user
from app.services.container import get_project_service
from app.services.project_service import ProjectService
from app.schemas.common import PaginationMeta

router = APIRouter(tags=["Projects"])

_AUTH_ERRORS = {
    401: {"model": ApiError, "description": "Authentication required."},
    403: {"model": ApiError, "description": "Project not owned by the caller."},
    404: {"model": ApiError, "description": "Project not found."},
}

ProjectSort = Literal["created_at", "updated_at", "name", "status"]
SortOrder = Literal["asc", "desc"]


def _owner(current_user: dict) -> str:
    owner = str(current_user.get("user_id") or "").strip()
    if not owner:
        raise AuthenticationError("Authentication required.")
    return owner


def _pagination(limit: int, offset: int, total: int, returned: int) -> PaginationMeta:
    return PaginationMeta(
        limit=limit,
        offset=offset,
        total=total,
        returned=returned,
        has_more=offset + returned < total,
    )


@router.post(
    "/projects",
    response_model=ProjectRecord,
    status_code=201,
    responses=_AUTH_ERRORS,
    summary="Create a project",
)
async def create_project(
    payload: ProjectCreateRequest,
    current_user: dict = Depends(get_current_user),
    service: ProjectService = Depends(get_project_service),
) -> ProjectRecord:
    """Create a project owned by the authenticated user."""
    row = service.create_project(_owner(current_user), payload)
    return ProjectRecord.model_validate(row)


@router.get(
    "/projects",
    response_model=ProjectsResponse,
    responses=_AUTH_ERRORS,
    summary="List the caller's projects",
)
async def list_projects(
    limit: int = Query(20, ge=1, le=100, description="Page size."),
    offset: int = Query(0, ge=0, le=1_000_000, description="Items to skip."),
    status: Optional[ProjectStatus] = Query(None, description="Filter by project status."),
    research_domain: Optional[str] = Query(
        None, max_length=120, description="Filter by research domain."
    ),
    q: Optional[str] = Query(
        None, max_length=200, description="Case-insensitive name/description search."
    ),
    created_after: Optional[datetime] = Query(
        None, description="Only projects created at or after this ISO-8601 timestamp."
    ),
    created_before: Optional[datetime] = Query(
        None, description="Only projects created at or before this ISO-8601 timestamp."
    ),
    sort: ProjectSort = Query("created_at", description="Sort field."),
    order: SortOrder = Query("desc", description="Sort direction."),
    current_user: dict = Depends(get_current_user),
    service: ProjectService = Depends(get_project_service),
) -> ProjectsResponse:
    rows, total = service.list_projects(
        _owner(current_user),
        status=status,
        research_domain=research_domain,
        q=q,
        created_after=created_after,
        created_before=created_before,
        sort=sort,
        order=order,
        limit=limit,
        offset=offset,
    )
    records = [ProjectRecord.model_validate(row) for row in rows]
    return ProjectsResponse(
        projects=records,
        count=len(records),
        pagination=_pagination(limit, offset, total, len(records)),
    )


@router.get(
    "/projects/{project_id}",
    response_model=ProjectRecord,
    responses=_AUTH_ERRORS,
    summary="Get one project",
)
async def get_project(
    project_id: UUID,
    current_user: dict = Depends(get_current_user),
    service: ProjectService = Depends(get_project_service),
) -> ProjectRecord:
    """Return a project owned by the caller (404 otherwise)."""
    row = service.get_project(str(project_id), _owner(current_user))
    return ProjectRecord.model_validate(row)


@router.patch(
    "/projects/{project_id}",
    response_model=ProjectRecord,
    responses={
        **_AUTH_ERRORS,
        400: {"model": ApiError, "description": "No fields supplied."},
        422: {"model": ApiError, "description": "Invalid field value or project ID."},
    },
    summary="Update a project",
)
async def update_project(
    project_id: UUID,
    payload: ProjectUpdateRequest,
    current_user: dict = Depends(get_current_user),
    service: ProjectService = Depends(get_project_service),
) -> ProjectRecord:
    """Patch name, description, research domain or status."""
    row = service.update_project(str(project_id), _owner(current_user), payload)
    return ProjectRecord.model_validate(row)


@router.delete(
    "/projects/{project_id}",
    response_model=DeletedProjectResponse,
    responses=_AUTH_ERRORS,
    summary="Delete a project and its research sessions",
)
async def delete_project(
    project_id: UUID,
    current_user: dict = Depends(get_current_user),
    service: ProjectService = Depends(get_project_service),
) -> DeletedProjectResponse:
    """Delete an owned project; its research sessions are removed too."""
    service.delete_project(str(project_id), _owner(current_user))
    return DeletedProjectResponse(status="deleted", project_id=str(project_id))


# ── Nested research sessions ────────────────────────────────────────────────


@router.post(
    "/projects/{project_id}/research",
    response_model=ResearchRecord,
    status_code=201,
    responses=_AUTH_ERRORS,
    summary="Create a research session in a project",
)
async def create_project_research(
    project_id: UUID,
    payload: ResearchCreateRequest,
    current_user: dict = Depends(get_current_user),
    service: ProjectService = Depends(get_project_service),
) -> ResearchRecord:
    """Record a research session under an owned project."""
    row = service.create_research(str(project_id), _owner(current_user), payload)
    return ResearchRecord.model_validate(row)


@router.get(
    "/projects/{project_id}/research",
    response_model=ResearchListResponse,
    responses={
        **_AUTH_ERRORS,
        400: {"model": ApiError, "description": "Invalid date range."},
    },
    summary="List research sessions in a project",
)
async def list_project_research(
    project_id: UUID,
    limit: int = Query(20, ge=1, le=100, description="Page size."),
    offset: int = Query(0, ge=0, le=1_000_000, description="Items to skip."),
    status: Optional[SessionStatus] = Query(None, description="Filter by session status."),
    provider: Optional[LLMProvider] = Query(None, description="Filter by LLM provider."),
    q: Optional[str] = Query(None, max_length=200, description="Topic search."),
    created_after: Optional[datetime] = Query(None),
    created_before: Optional[datetime] = Query(None),
    sort: Literal["created_at", "updated_at", "topic", "status", "priority"] = Query(
        "created_at"
    ),
    order: SortOrder = Query("desc"),
    current_user: dict = Depends(get_current_user),
    service: ProjectService = Depends(get_project_service),
) -> ResearchListResponse:
    rows, total = service.list_research(
        str(project_id),
        _owner(current_user),
        status=status,
        provider=provider,
        q=q,
        created_after=created_after,
        created_before=created_before,
        sort=sort,
        order=order,
        limit=limit,
        offset=offset,
    )
    records = [ResearchRecord.model_validate(row) for row in rows]
    return ResearchListResponse(
        research_sessions=records,
        count=len(records),
        pagination=_pagination(limit, offset, total, len(records)),
    )
