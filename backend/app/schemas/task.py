"""Research task schema: one unit of work inside a research project."""

from __future__ import annotations

from typing import List, Optional
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from app.schemas.base import LongText, MediumText, OutputSchema, Title, UtcDatetime, utc_now
from app.schemas.enums import AgentName, ResearchPriority, TaskStatus
from app.schemas.research import SubQuery


class ResearchTask(OutputSchema):
    """A queued, running or finished unit of research work."""

    task_id: UUID = Field(default_factory=uuid4)
    project_id: Optional[UUID] = None
    session_id: Optional[UUID] = None
    title: Title
    description: Optional[LongText] = None
    status: TaskStatus = TaskStatus.QUEUED
    priority: ResearchPriority = ResearchPriority.NORMAL
    sub_queries: List[SubQuery] = Field(default_factory=list, max_length=12)
    assigned_agent: Optional[AgentName] = None
    depends_on: List[UUID] = Field(default_factory=list, max_length=20)
    attempts: int = Field(0, ge=0, le=10)
    created_at: UtcDatetime = Field(default_factory=utc_now)
    started_at: Optional[UtcDatetime] = None
    completed_at: Optional[UtcDatetime] = None
    error: Optional[MediumText] = None

    @model_validator(mode="after")
    def _validate_timeline(self) -> "ResearchTask":
        if self.started_at is not None and self.started_at < self.created_at:
            raise ValueError("started_at must be greater than or equal to created_at")
        if self.completed_at is not None:
            reference = self.started_at or self.created_at
            if self.completed_at < reference:
                raise ValueError(
                    "completed_at must be greater than or equal to started_at/created_at"
                )
        if self.task_id in self.depends_on:
            raise ValueError("a task cannot depend on itself")
        return self
