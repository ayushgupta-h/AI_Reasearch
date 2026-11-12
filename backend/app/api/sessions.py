"""Session history endpoints backed by the SQLite repository."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from app.errors import NotFoundError
from app.observability import current_request_id
from app.schemas.common import PaginationMeta
from app.schemas.envelopes import SessionsEnvelope
from app.schemas.errors import ApiError
from app.schemas.responses import (
    DeletedSessionResponse,
    SessionRecord,
    SessionsResponse,
)
from app.security.dependencies import get_current_user, get_optional_user
from app.services.container import get_session_service
from app.services.session_service import SessionService

router = APIRouter()

_ENVELOPE_NOTE = (
    "Flat payload by default (backward compatible); with `envelope=true` the "
    "response is wrapped in the standardized ApiResponse envelope."
)


@router.get(
    "/sessions",
    tags=["System"],
    summary="List research sessions",
    responses={
        200: {
            "description": f"Session history. {_ENVELOPE_NOTE}",
            "content": {
                "application/json": {
                    "schema": {
                        "oneOf": [
                            {"$ref": "#/components/schemas/SessionsResponse"},
                            {"$ref": "#/components/schemas/SessionsEnvelope"},
                        ]
                    }
                }
            },
        }
    },
)
async def list_sessions(
    request: Request,
    envelope: bool = Query(False, description="Wrap the payload in ApiResponse."),
    limit: Optional[int] = Query(
        None,
        ge=1,
        le=100,
        description="Page size. Omit to return all sessions (legacy behaviour).",
    ),
    offset: int = Query(0, ge=0, le=1_000_000, description="Number of sessions to skip."),
    current_user: Optional[dict] = Depends(get_optional_user),
    service: SessionService = Depends(get_session_service),
) -> JSONResponse:
    """Return past research sessions. When authenticated, strictly filter by user ID."""
    _ = request
    user_id = current_user.get("user_id") if current_user else None
    rows = service.list_sessions(user_id=user_id)
    total = len(rows)

    window = rows[offset:] if limit is None else rows[offset : offset + limit]
    records = [SessionRecord.model_validate(row) for row in window]

    pagination = None
    if limit is not None:
        pagination = PaginationMeta(
            limit=limit,
            offset=offset,
            total=total,
            returned=len(records),
            has_more=offset + len(records) < total,
        )

    payload = SessionsResponse(sessions=records, count=len(records), pagination=pagination)

    if envelope:
        body = SessionsEnvelope(
            data=payload,
            message="Sessions retrieved",
            request_id=current_request_id(),
        ).to_payload(exclude_none=True)
        return JSONResponse(body)

    # Legacy shape: no `pagination` key unless pagination was requested.
    body = payload.model_dump(mode="json")
    if pagination is None:
        body.pop("pagination", None)
    return JSONResponse(body)


@router.delete(
    "/sessions/{session_id}",
    response_model=DeletedSessionResponse,
    tags=["System"],
    responses={
        401: {"model": ApiError, "description": "Authentication required."},
        404: {"model": ApiError, "description": "Session not found or not owned."},
    },
)
async def delete_session_endpoint(
    session_id: str,
    current_user: dict = Depends(get_current_user),
    service: SessionService = Depends(get_session_service),
) -> DeletedSessionResponse:
    """Delete a research session record owned by the authenticated user."""
    deleted = service.delete_session(session_id, user_id=current_user["user_id"])
    if not deleted:
        raise NotFoundError("Session not found or not owned by user.")
    return DeletedSessionResponse(status="deleted", session_id=session_id)
