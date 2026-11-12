"""API schemas for agent execution history and research timeline."""

from __future__ import annotations

from typing import Annotated, List, Optional

from pydantic import Field, StringConstraints

from app.schemas.base import OutputSchema, UtcDatetime
from app.schemas.common import PaginationMeta
from app.schemas.enums import AgentExecutionStatus, SessionStatus, TimelineEventType

ExecutionId = Annotated[
    str, StringConstraints(pattern=r"^[a-f0-9]{32}$", min_length=32, max_length=32)
]
ResearchId = Annotated[
    str, StringConstraints(pattern=r"^[a-f0-9]{32}$", min_length=32, max_length=32)
]
AgentNameText = Annotated[
    str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$", min_length=1, max_length=64)
]


class AgentExecutionRecord(OutputSchema):
    """One real execution of an agent stage."""

    execution_id: ExecutionId
    research_id: ResearchId
    agent_name: AgentNameText
    status: AgentExecutionStatus
    started_at: UtcDatetime
    finished_at: Optional[UtcDatetime] = None
    duration_ms: Optional[float] = Field(None, ge=0, le=86_400_000)
    retry_count: int = Field(0, ge=0, le=1_000)
    error_summary: Optional[str] = Field(None, max_length=300)
    output_reference: Optional[str] = Field(None, max_length=500)


class AgentExecutionsResponse(OutputSchema):
    research_id: ResearchId
    executions: List[AgentExecutionRecord]
    count: int = Field(ge=0)
    pagination: PaginationMeta


class TimelineEventRecord(OutputSchema):
    event_id: ExecutionId
    research_id: ResearchId
    event_type: TimelineEventType
    agent_name: Optional[AgentNameText] = None
    message: str = Field(..., min_length=1, max_length=600)
    execution_id: Optional[ExecutionId] = None
    occurred_at: UtcDatetime


class ResearchTimelineResponse(OutputSchema):
    research_id: ResearchId
    events: List[TimelineEventRecord]
    count: int = Field(ge=0)
    pagination: PaginationMeta


class ResearchProgressResponse(OutputSchema):
    research_id: ResearchId
    status: SessionStatus
    total_executions: int = Field(ge=0)
    completed_executions: int = Field(ge=0)
    failed_executions: int = Field(ge=0)
    running_executions: int = Field(ge=0)
    pending_executions: int = Field(ge=0)
    percent_complete: float = Field(ge=0, le=100)
    current_agent: Optional[AgentNameText] = None
    started_at: Optional[UtcDatetime] = None
    updated_at: Optional[UtcDatetime] = None
    latest_event: Optional[str] = Field(None, max_length=600)


__all__ = [
    "AgentExecutionRecord",
    "AgentExecutionsResponse",
    "AgentNameText",
    "ExecutionId",
    "ResearchId",
    "ResearchProgressResponse",
    "ResearchTimelineResponse",
    "TimelineEventRecord",
]
