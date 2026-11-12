"""
app/repositories/postgres/projects.py
=====================================
Projects repository with ownership scoping and pagination.
"""

from __future__ import annotations

from typing import Optional, Sequence, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models.domain import Project, ResearchSession
from app.repositories.postgres.base import PostgresRepository


class ProjectRepository(PostgresRepository[Project]):
    model = Project
    owner_column = "owner_id"
    default_sort = "created_at"
    allowed_sorts = ("created_at", "updated_at", "name", "status")

    def __init__(self, session: Session) -> None:
        super().__init__(session)

    def list_active(
        self, *, owner_id: str, limit: int = 20, offset: int = 0
    ) -> Tuple[Sequence[Project], int]:
        return self.list(owner_id=owner_id, limit=limit, offset=offset)

    def sessions(self, project: Project) -> Sequence[ResearchSession]:
        return self.session.scalars(
            select(ResearchSession)
            .where(ResearchSession.project_id == project.id)
            .order_by(ResearchSession.created_at.desc())
        ).all()


__all__ = ["ProjectRepository"]
