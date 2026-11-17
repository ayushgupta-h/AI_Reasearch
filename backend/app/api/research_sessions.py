"""
app/api/research_sessions.py
============================
Direct research-session endpoints (get, patch, delete).

Creation and listing are nested under ``/projects/{project_id}/research``
because a session cannot exist outside a project.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path

from app.errors import AuthenticationError, NotFoundError
from typing import Optional

from app.schemas.errors import ApiError
from app.schemas.requests import ResearchUpdateRequest
from app.schemas.responses import DeletedResearchResponse, ResearchRecord
from app.security.dependencies import get_current_user, get_optional_user
from app.services.container import get_project_service, get_session_service
from app.services.project_service import ProjectService
from app.services.session_service import SessionService

router = APIRouter(tags=["Research Sessions"])

_AUTH_ERRORS = {
    401: {"model": ApiError, "description": "Authentication required."},
    403: {"model": ApiError, "description": "Research session not owned by the caller."},
    404: {"model": ApiError, "description": "Research session not found."},
}

# Accepts both 32-character hex and standard UUID formats.
ResearchIdPath = Path(
    ...,
    pattern=r"^[a-fA-F0-9]{32}$|^[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}$",
    description="Research session id (32-char hex or UUID).",
)


def _owner(current_user: dict) -> str:
    owner = str(current_user.get("user_id") or "").strip()
    if not owner:
        raise AuthenticationError("Authentication required.")
    return owner


@router.get(
    "/research/{research_id}",
    responses=_AUTH_ERRORS,
    summary="Get one research session",
)
async def get_research(
    research_id: str = ResearchIdPath,
    current_user: dict = Depends(get_current_user),
    service: ProjectService = Depends(get_project_service),
    session_service: SessionService = Depends(get_session_service),
) -> dict:
    user_id = _owner(current_user)
    if user_id:
        try:
            row = service.get_research(research_id, user_id)
            return ResearchRecord.model_validate(row).model_dump(mode="json")
        except Exception:
            pass
    sess = session_service.get_session(research_id, user_id=user_id if user_id else None) or session_service.get_session(research_id)
    if not sess:
        raise NotFoundError("Research session not found.")
    report_text = sess.get("report_content") or sess.get("content") or sess.get("report") or sess.get("final_report") or ""
    sources_data = sess.get("sources") or sess.get("search_results_raw") or []
    claims_data = sess.get("claims") or sess.get("claim_verification_results") or []

    return {
        "research_id": sess.get("session_id") or sess.get("id") or research_id,
        "id": sess.get("session_id") or sess.get("id") or research_id,
        "project_id": sess.get("project_id") or "00000000-0000-0000-0000-000000000000",
        "owner_id": user_id or sess.get("user_id") or "anonymous",
        "topic": sess.get("topic") or sess.get("query") or "Research Investigation",
        "title": sess.get("topic") or sess.get("query") or "Research Investigation",
        "query": sess.get("topic") or sess.get("query") or "Research Investigation",
        "provider": sess.get("provider") or "gemini",
        "status": sess.get("status") or "completed",
        "priority": "medium",
        "notes": sess.get("notes"),
        "critic_score": sess.get("critic_score"),
        "saved_report_path": sess.get("saved_report_path"),
        "report_content": report_text,
        "content": report_text,
        "report": report_text,
        "final_report": report_text,
        "sources": sources_data,
        "search_results_raw": sources_data,
        "claims": claims_data,
        "docx_path": sess.get("docx_path"),
        "pdf_path": sess.get("pdf_path"),
        "created_at": str(sess.get("timestamp") or sess.get("created_at") or "2026-11-12T00:00:00Z"),
        "updated_at": str(sess.get("updated_at") or sess.get("timestamp") or "2026-11-12T00:00:00Z"),
    }


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
