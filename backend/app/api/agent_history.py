"""
app/api/agent_history.py
========================
Read-only agent execution history for one research run.

All four endpoints require a verified JWT and check that the run belongs to the
caller. History is served from rows written while the pipeline executed; if a
run has not executed an agent yet, the list is simply empty.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Path, Query

from app.errors import AuthenticationError
from app.schemas.agent_history import (
    AgentExecutionRecord,
    AgentExecutionsResponse,
    ResearchProgressResponse,
    ResearchTimelineResponse,
    TimelineEventRecord,
)
from app.schemas.common import PaginationMeta
from app.schemas.enums import AgentExecutionStatus, TimelineEventType
from app.schemas.errors import ApiError
from app.security.dependencies import get_current_user
from app.services.agent_history_service import AgentHistoryService
from app.services.container import get_agent_history_service

router = APIRouter(tags=["Agent History"])

ResearchIdPath = Path(
    ...,
    pattern=r"^[a-f0-9]{32}$",
    description="32-character lowercase hex research id.",
)
ExecutionIdPath = Path(
    ...,
    pattern=r"^[a-f0-9]{32}$",
    description="32-character lowercase hex execution id.",
)

_AGENT_NAME_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"
_ERRORS = {
    401: {"model": ApiError, "description": "Authentication required."},
    403: {"model": ApiError, "description": "Research run not owned by the caller."},
    404: {"model": ApiError, "description": "Research run or execution not found."},
}


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


@router.get(
    "/research/{research_id}/agents",
    response_model=AgentExecutionsResponse,
    responses=_ERRORS,
    summary="List agent executions for a research run",
)
async def list_agent_executions(
    research_id: str = ResearchIdPath,
    limit: int = Query(20, ge=1, le=100, description="Page size."),
    offset: int = Query(0, ge=0, le=1_000_000, description="Executions to skip."),
    status: Optional[AgentExecutionStatus] = Query(
        None, description="Filter by execution status."
    ),
    agent_name: Optional[str] = Query(
        None, pattern=_AGENT_NAME_PATTERN, description="Filter by agent name."
    ),
    started_after: Optional[datetime] = Query(
        None, description="Only executions started at or after this timestamp."
    ),
    started_before: Optional[datetime] = Query(
        None, description="Only executions started at or before this timestamp."
    ),
    sort: Literal["started_at", "agent_name", "status", "duration_ms"] = Query(
        "started_at", description="Sort field."
    ),
    order: Literal["asc", "desc"] = Query("asc", description="Sort direction."),
    current_user: dict = Depends(get_current_user),
    service: AgentHistoryService = Depends(get_agent_history_service),
) -> AgentExecutionsResponse:
    rows, total = service.list_executions(
        research_id,
        _owner(current_user),
        status=status,
        agent_name=agent_name,
        started_after=started_after,
        started_before=started_before,
        sort=sort,
        order=order,
        limit=limit,
        offset=offset,
    )
    records = [AgentExecutionRecord.model_validate(row) for row in rows]
    return AgentExecutionsResponse(
        research_id=research_id,
        executions=records,
        count=len(records),
        pagination=_pagination(limit, offset, total, len(records)),
    )


@router.get(
    "/research/{research_id}/agents/{execution_id}",
    response_model=AgentExecutionRecord,
    responses=_ERRORS,
    summary="Get one agent execution",
)
async def get_agent_execution(
    research_id: str = ResearchIdPath,
    execution_id: str = ExecutionIdPath,
    current_user: dict = Depends(get_current_user),
    service: AgentHistoryService = Depends(get_agent_history_service),
) -> AgentExecutionRecord:
    row = service.get_execution(research_id, execution_id, _owner(current_user))
    return AgentExecutionRecord.model_validate(row)


@router.get(
    "/research/{research_id}/timeline",
    response_model=ResearchTimelineResponse,
    responses=_ERRORS,
    summary="Chronological research timeline",
)
async def get_research_timeline(
    research_id: str = ResearchIdPath,
    limit: int = Query(50, ge=1, le=200, description="Page size."),
    offset: int = Query(0, ge=0, le=1_000_000, description="Events to skip."),
    event_type: Optional[TimelineEventType] = Query(
        None, description="Filter by event type."
    ),
    agent_name: Optional[str] = Query(
        None, pattern=_AGENT_NAME_PATTERN, description="Filter by agent name."
    ),
    occurred_after: Optional[datetime] = Query(None),
    occurred_before: Optional[datetime] = Query(None),
    current_user: dict = Depends(get_current_user),
    service: AgentHistoryService = Depends(get_agent_history_service),
) -> ResearchTimelineResponse:
    rows, total = service.timeline(
        research_id,
        _owner(current_user),
        event_type=event_type,
        agent_name=agent_name,
        occurred_after=occurred_after,
        occurred_before=occurred_before,
        limit=limit,
        offset=offset,
    )
    events = [TimelineEventRecord.model_validate(row) for row in rows]
    return ResearchTimelineResponse(
        research_id=research_id,
        events=events,
        count=len(events),
        pagination=_pagination(limit, offset, total, len(events)),
    )


@router.get(
    "/research/{research_id}/progress",
    response_model=ResearchProgressResponse,
    responses=_ERRORS,
    summary="Aggregate progress for a research run",
)
async def get_research_progress(
    research_id: str = ResearchIdPath,
    current_user: dict = Depends(get_current_user),
    service: AgentHistoryService = Depends(get_agent_history_service),
) -> ResearchProgressResponse:
    payload = service.progress(research_id, _owner(current_user))
    return ResearchProgressResponse.model_validate(payload)
