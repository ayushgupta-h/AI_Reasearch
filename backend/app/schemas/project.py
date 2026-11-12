"""Project schema: a container that groups research sessions and tasks."""

from __future__ import annotations

from typing import List, Optional
from uuid import UUID, uuid4

from pydantic import Field

from app.schemas.base import LongText, Name, OutputSchema, Tag, TimestampedSchema
from app.schemas.enums import ProjectStatus


class Project(TimestampedSchema):
    """A research project grouping related runs."""

    project_id: UUID = Field(default_factory=uuid4)
    name: Name
    description: Optional[LongText] = None
    owner_id: Optional[UUID] = None
    status: ProjectStatus = ProjectStatus.ACTIVE
    tags: List[Tag] = Field(default_factory=list, max_length=20)
    session_ids: List[UUID] = Field(default_factory=list, max_length=1_000)


__all__ = ["Project", "ProjectStatus"]
