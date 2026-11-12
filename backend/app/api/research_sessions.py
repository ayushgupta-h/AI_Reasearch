"""
app/api/research_sessions.py
============================
Direct research-session endpoints (get, patch, delete).

Creation and listing are nested under ``/projects/{project_id}/research``
because a session cannot exist outside a project.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path

from app.errors import AuthenticationError
from app.schemas.errors import ApiError
from app.schemas.requests import ResearchUpdateRequest
from app.schemas.responses import DeletedResearchResponse, ResearchRecord
from app.security.dependencies import get_current_user
from app.services.container import get_project_service
from app.services.project_service import ProjectService

router = APIRouter(tags=["Research Sessions"])

_AUTH_ERRORS = {
    401: {"model": ApiError, "description": "Authentication required."},
    403: {"model": ApiError, "description": "Research session not owned by the caller."},
    404: {"model": ApiError, "description": "Research session not found."},
}

# Same id format the SSE pipeline emits (uuid4().hex).
ResearchIdPath = Path(
    ...,
    pattern=r"^[a-f0-9]{32}$",
    description="32-character lowercase hex research id.",
)


def _owner(current_user: dict) -> str:
    owner = str(current_user.get("user_id") or "").strip()
    if not owner:
        raise AuthenticationError("Authentication required.")
    return owner


@router.get(
    "/research/{research_id}",
    response_model=ResearchRecord,
    responses=_AUTH_ERRORS,
    summary="Get one research session",
)
async def get_research(
    research_id: str = ResearchIdPath,
    current_user: dict = Depends(get_current_user),
    service: ProjectService = Depends(get_project_service),
) -> ResearchRecord:
    row = service.get_research(research_id, _owner(current_user))
    return ResearchRecord.model_validate(row)


@router.patch(
    "/research/{research_id}",
    response_model=ResearchRecord,
    responses={
        **_AUTH_ERRORS,
        400: {"model": ApiError, "description": "No fields supplied."},
        422: {"model": ApiError, "description": "Invalid field value or research ID."},
    },
    summary="Update a research session",
)
async def update_research(
    payload: ResearchUpdateRequest,
    research_id: str = ResearchIdPath,
    current_user: dict = Depends(get_current_user),
    service: ProjectService = Depends(get_project_service),
) -> ResearchRecord:
    row = service.update_research(research_id, _owner(current_user), payload)
    return ResearchRecord.model_validate(row)


@router.delete(
    "/research/{research_id}",
    response_model=DeletedResearchResponse,
    responses=_AUTH_ERRORS,
    summary="Delete a research session",
)
async def delete_research(
    research_id: str = ResearchIdPath,
    current_user: dict = Depends(get_current_user),
    service: ProjectService = Depends(get_project_service),
) -> DeletedResearchResponse:
    service.delete_research(research_id, _owner(current_user))
    return DeletedResearchResponse(status="deleted", research_id=research_id)
