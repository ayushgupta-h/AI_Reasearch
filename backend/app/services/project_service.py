"""
app/services/project_service.py
===============================
Business rules for projects and their research sessions.

Ownership is always supplied by the caller from the authenticated JWT identity
— never from a request body. The service verifies project ownership before any
nested research operation and raises the same ``NotFoundError`` for "missing"
and "someone else's" so IDs cannot be probed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.errors import AuthenticationError, BadRequestError, NotFoundError
from app.observability import get_logger
from app.repositories.projects import ProjectRepository
from app.schemas.requests import (
    ProjectCreateRequest,
    ProjectUpdateRequest,
    ResearchCreateRequest,
    ResearchUpdateRequest,
)
from app.services.agent_history_service import AgentHistoryService

logger = get_logger("app.services.projects")


class ProjectService:
    def __init__(
        self,
        repository: ProjectRepository,
        history: Optional[AgentHistoryService] = None,
    ) -> None:
        self._repository = repository
        self._history = history

    # ── Projects ──────────────────────────────────────────────────────────

    def create_project(
        self, owner_id: str, payload: ProjectCreateRequest
    ) -> Dict[str, Any]:
        owner = self._require_owner(owner_id)
        row = self._repository.create_project(
            owner_id=owner,
            name=payload.name,
            description=payload.description,
            research_domain=payload.research_domain,
            status=payload.status.value,
        )
        logger.info("project.created", extra={"project_id": row["project_id"]})
        return row

    def list_projects(
        self,
        owner_id: str,
        *,
        status: Optional[Any] = None,
        research_domain: Optional[str] = None,
        q: Optional[str] = None,
        created_after: Optional[datetime] = None,
        created_before: Optional[datetime] = None,
        sort: str = "created_at",
        order: str = "desc",
        limit: int = 20,
        offset: int = 0,
    ) -> Tuple[List[Dict[str, Any]], int]:
        owner = self._require_owner(owner_id)
        self._validate_window(created_after, created_before)
        return self._repository.list_projects(
            owner_id=owner,
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

    def get_project(self, project_id: str, owner_id: str) -> Dict[str, Any]:
        owner = self._require_owner(owner_id)
        row = self._repository.get_project(project_id, owner)
        if row is None:
            raise NotFoundError("Project not found.")
        return row

    def update_project(
        self, project_id: str, owner_id: str, payload: ProjectUpdateRequest
    ) -> Dict[str, Any]:
        owner = self._require_owner(owner_id)
        changes = payload.model_dump(exclude_unset=True, mode="json")
        if not changes:
            raise BadRequestError("No project fields were provided to update.")

        row = self._repository.update_project(project_id, owner, changes)
        if row is None:
            raise NotFoundError("Project not found.")
        logger.info("project.updated", extra={"project_id": project_id})
        return row

    def delete_project(self, project_id: str, owner_id: str) -> None:
        owner = self._require_owner(owner_id)
        if not self._repository.delete_project(project_id, owner):
            raise NotFoundError("Project not found.")
        logger.info("project.deleted", extra={"project_id": project_id})

    # ── Research sessions ─────────────────────────────────────────────────

    def create_research(
        self, project_id: str, owner_id: str, payload: ResearchCreateRequest
    ) -> Dict[str, Any]:
        owner = self._require_owner(owner_id)
        # Ownership check first: a foreign project must be indistinguishable
        # from a missing one.
        self.get_project(project_id, owner)

        row = self._repository.create_research(
            project_id=project_id,
            owner_id=owner,
            topic=payload.topic,
            provider=payload.provider,
            status="pending",
            priority=payload.priority.value,
            notes=payload.notes,
        )
        if self._history is not None:
            try:
                self._history.register_research(
                    row["research_id"], owner, row["topic"]
                )
            except Exception:  # history must not block research creation
                logger.exception("research.history_register_failed")
        logger.info(
            "research.created",
            extra={"research_id": row["research_id"], "project_id": project_id},
        )
        return row

    def list_research(
        self,
        project_id: str,
        owner_id: str,
        *,
        status: Optional[Any] = None,
        provider: Optional[str] = None,
        q: Optional[str] = None,
        created_after: Optional[datetime] = None,
        created_before: Optional[datetime] = None,
        sort: str = "created_at",
        order: str = "desc",
        limit: int = 20,
        offset: int = 0,
    ) -> Tuple[List[Dict[str, Any]], int]:
        owner = self._require_owner(owner_id)
        self.get_project(project_id, owner)
        self._validate_window(created_after, created_before)
        return self._repository.list_research(
            project_id=project_id,
            owner_id=owner,
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

    def get_research(self, research_id: str, owner_id: str) -> Dict[str, Any]:
        owner = self._require_owner(owner_id)
        row = self._repository.get_research(research_id, owner)
        if row is None:
            raise NotFoundError("Research session not found.")
        return row

    def update_research(
        self, research_id: str, owner_id: str, payload: ResearchUpdateRequest
    ) -> Dict[str, Any]:
        owner = self._require_owner(owner_id)
        changes = payload.model_dump(exclude_unset=True, mode="json")
        if not changes:
            raise BadRequestError("No research fields were provided to update.")

        row = self._repository.update_research(research_id, owner, changes)
        if row is None:
            raise NotFoundError("Research session not found.")
        logger.info("research.updated", extra={"research_id": research_id})
        return row

    def delete_research(self, research_id: str, owner_id: str) -> None:
        owner = self._require_owner(owner_id)
        if not self._repository.delete_research(research_id, owner):
            raise NotFoundError("Research session not found.")
        logger.info("research.deleted", extra={"research_id": research_id})

    # ── Validation ────────────────────────────────────────────────────────

    @staticmethod
    def _require_owner(owner_id: Optional[str]) -> str:
        owner = (owner_id or "").strip()
        if not owner:
            raise AuthenticationError("Authentication required.")
        return owner

    @staticmethod
    def _validate_window(
        created_after: Optional[datetime], created_before: Optional[datetime]
    ) -> None:
        if created_after is None or created_before is None:
            return
        start = created_after
        end = created_before
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        if start > end:
            raise BadRequestError("created_after must be earlier than created_before.")


__all__ = ["ProjectService"]
