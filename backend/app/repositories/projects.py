"""
app/repositories/projects.py
============================
SQL persistence for projects and their research sessions.

The project uses SQLite (``SessionManager`` owns the legacy ``research_sessions``
table). These repositories add two new tables in the same database file via the
``Database`` port; no PostgreSQL/SQLAlchemy layer exists in this repository.

Every read and write is scoped by ``owner_id`` in the SQL itself, so a caller
cannot reach another user's rows even if a service check is bypassed. Sorting
columns come from a whitelist, never from raw user input.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from app.infrastructure.ports import Database

_CREATE_PROJECTS = """
CREATE TABLE IF NOT EXISTS projects (
    project_id      TEXT PRIMARY KEY,
    owner_id        TEXT NOT NULL,
    name            TEXT NOT NULL,
    description     TEXT,
    research_domain TEXT,
    status          TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
"""

_CREATE_RESEARCH = """
CREATE TABLE IF NOT EXISTS project_research (
    research_id TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL,
    owner_id    TEXT NOT NULL,
    topic       TEXT NOT NULL,
    provider    TEXT,
    status      TEXT NOT NULL,
    priority    TEXT NOT NULL,
    notes       TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
"""

_CREATE_PROJECT_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_projects_owner "
    "ON projects(owner_id, created_at);"
)
_CREATE_RESEARCH_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_project_research_owner "
    "ON project_research(owner_id, project_id, created_at);"
)

_PROJECT_COLUMNS = (
    "project_id, owner_id, name, description, research_domain, "
    "status, created_at, updated_at"
)
_RESEARCH_COLUMNS = (
    "research_id, project_id, owner_id, topic, provider, status, "
    "priority, notes, created_at, updated_at"
)

_PROJECT_SORTS = {
    "created_at": "created_at",
    "updated_at": "updated_at",
    "name": "name",
    "status": "status",
}
_RESEARCH_SORTS = {
    "created_at": "created_at",
    "updated_at": "updated_at",
    "topic": "topic",
    "status": "status",
    "priority": "priority",
}

_PROJECT_UPDATABLE = {"name", "description", "research_domain", "status"}
_RESEARCH_UPDATABLE = {"topic", "provider", "status", "priority", "notes"}


def utc_iso(value: Optional[datetime] = None) -> str:
    """Stable, sortable UTC timestamp string."""
    moment = value or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    return getattr(value, "value", str(value))


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class ProjectRepository:
    """CRUD for projects and their research sessions."""

    def __init__(self, database: Database) -> None:
        self._db = database
        self._schema_ready = False
        self._schema_lock = threading.Lock()

    # ── Schema ────────────────────────────────────────────────────────────

    def ensure_schema(self) -> None:
        if self._schema_ready:
            return
        with self._schema_lock:
            if self._schema_ready:
                return
            self._db.execute(_CREATE_PROJECTS)
            self._db.execute(_CREATE_RESEARCH)
            self._db.execute(_CREATE_PROJECT_INDEX)
            self._db.execute(_CREATE_RESEARCH_INDEX)
            self._schema_ready = True

    # ── Projects ──────────────────────────────────────────────────────────

    def create_project(
        self,
        *,
        owner_id: str,
        name: str,
        description: Optional[str],
        research_domain: Optional[str],
        status: str,
    ) -> Dict[str, Any]:
        self.ensure_schema()
        project_id = str(uuid.uuid4())
        now = utc_iso()
        self._db.execute(
            """
            INSERT INTO projects (
                project_id, owner_id, name, description, research_domain,
                status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (project_id, owner_id, name, description, research_domain, status, now, now),
        )
        created = self.get_project(project_id, owner_id)
        if created is None:  # pragma: no cover - insert just succeeded
            raise RuntimeError("Project insert was not readable back")
        return created

    def get_project(self, project_id: str, owner_id: str) -> Optional[Dict[str, Any]]:
        self.ensure_schema()
        return self._db.fetch_one(
            f"SELECT {_PROJECT_COLUMNS} FROM projects "
            "WHERE project_id = ? AND owner_id = ?",
            (project_id, owner_id),
        )

    def list_projects(
        self,
        *,
        owner_id: str,
        status: Optional[str] = None,
        research_domain: Optional[str] = None,
        q: Optional[str] = None,
        created_after: Optional[datetime] = None,
        created_before: Optional[datetime] = None,
        sort: str = "created_at",
        order: str = "desc",
        limit: int = 20,
        offset: int = 0,
    ) -> Tuple[List[Dict[str, Any]], int]:
        self.ensure_schema()
        clause, params = self._project_filters(
            owner_id=owner_id,
            status=status,
            research_domain=research_domain,
            q=q,
            created_after=created_after,
            created_before=created_before,
        )
        column = _PROJECT_SORTS.get(sort, "created_at")
        direction = "ASC" if order.lower() == "asc" else "DESC"

        total_row = self._db.fetch_one(
            f"SELECT COUNT(*) AS total FROM projects WHERE {clause}", params
        )
        total = int((total_row or {}).get("total", 0))
        rows = self._db.fetch_all(
            f"SELECT {_PROJECT_COLUMNS} FROM projects WHERE {clause} "
            f"ORDER BY {column} {direction}, project_id ASC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )
        return rows, total

    def update_project(
        self, project_id: str, owner_id: str, changes: Mapping[str, Any]
    ) -> Optional[Dict[str, Any]]:
        self.ensure_schema()
        updates = {
            key: _text(value)
            for key, value in changes.items()
            if key in _PROJECT_UPDATABLE
        }
        if not updates:
            return self.get_project(project_id, owner_id)
        updates["updated_at"] = utc_iso()

        assignments = ", ".join(f"{column} = ?" for column in updates)
        self._db.execute(
            f"UPDATE projects SET {assignments} "
            "WHERE project_id = ? AND owner_id = ?",
            (*updates.values(), project_id, owner_id),
        )
        return self.get_project(project_id, owner_id)

    def delete_project(self, project_id: str, owner_id: str) -> bool:
        self.ensure_schema()
        with self._db.transaction() as tx:
            owned = tx.fetch_one(
                "SELECT 1 AS ok FROM projects WHERE project_id = ? AND owner_id = ?",
                (project_id, owner_id),
            )
            if owned is None:
                return False
            # No reliance on FK pragma: remove children explicitly, same owner.
            tx.execute(
                "DELETE FROM project_research WHERE project_id = ? AND owner_id = ?",
                (project_id, owner_id),
            )
            tx.execute(
                "DELETE FROM projects WHERE project_id = ? AND owner_id = ?",
                (project_id, owner_id),
            )
        return True

    # ── Research sessions ─────────────────────────────────────────────────

    def create_research(
        self,
        *,
        project_id: str,
        owner_id: str,
        topic: str,
        provider: Optional[str],
        status: str,
        priority: str,
        notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.ensure_schema()
        research_id = uuid.uuid4().hex
        now = utc_iso()
        self._db.execute(
            """
            INSERT INTO project_research (
                research_id, project_id, owner_id, topic, provider, status,
                priority, notes, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                research_id,
                project_id,
                owner_id,
                topic,
                provider,
                status,
                priority,
                notes,
                now,
                now,
            ),
        )
        created = self.get_research(research_id, owner_id)
        if created is None:  # pragma: no cover - insert just succeeded
            raise RuntimeError("Research insert was not readable back")
        return created

    def get_research(
        self, research_id: str, owner_id: str
    ) -> Optional[Dict[str, Any]]:
        self.ensure_schema()
        return self._db.fetch_one(
            f"SELECT {_RESEARCH_COLUMNS} FROM project_research "
            "WHERE research_id = ? AND owner_id = ?",
            (research_id, owner_id),
        )

    def list_research(
        self,
        *,
        project_id: str,
        owner_id: str,
        status: Optional[str] = None,
        provider: Optional[str] = None,
        q: Optional[str] = None,
        created_after: Optional[datetime] = None,
        created_before: Optional[datetime] = None,
        sort: str = "created_at",
        order: str = "desc",
        limit: int = 20,
        offset: int = 0,
    ) -> Tuple[List[Dict[str, Any]], int]:
        self.ensure_schema()
        where = ["project_id = ?", "owner_id = ?"]
        params: List[Any] = [project_id, owner_id]

        if status is not None:
            where.append("status = ?")
            params.append(_text(status))
        if provider is not None:
            where.append("provider = ?")
            params.append(_text(provider))
        if q:
            pattern = f"%{_escape_like(q)}%"
            where.append("topic LIKE ? ESCAPE '\\'")
            params.append(pattern)
        if created_after is not None:
            where.append("created_at >= ?")
            params.append(utc_iso(created_after))
        if created_before is not None:
            where.append("created_at <= ?")
            params.append(utc_iso(created_before))

        clause = " AND ".join(where)
        column = _RESEARCH_SORTS.get(sort, "created_at")
        direction = "ASC" if order.lower() == "asc" else "DESC"

        total_row = self._db.fetch_one(
            f"SELECT COUNT(*) AS total FROM project_research WHERE {clause}", params
        )
        total = int((total_row or {}).get("total", 0))
        rows = self._db.fetch_all(
            f"SELECT {_RESEARCH_COLUMNS} FROM project_research WHERE {clause} "
            f"ORDER BY {column} {direction}, research_id ASC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )
        return rows, total

    def update_research(
        self, research_id: str, owner_id: str, changes: Mapping[str, Any]
    ) -> Optional[Dict[str, Any]]:
        self.ensure_schema()
        updates = {
            key: _text(value)
            for key, value in changes.items()
            if key in _RESEARCH_UPDATABLE
        }
        if not updates:
            return self.get_research(research_id, owner_id)
        updates["updated_at"] = utc_iso()

        assignments = ", ".join(f"{column} = ?" for column in updates)
        self._db.execute(
            f"UPDATE project_research SET {assignments} "
            "WHERE research_id = ? AND owner_id = ?",
            (*updates.values(), research_id, owner_id),
        )
        return self.get_research(research_id, owner_id)

    def delete_research(
        self, research_id: str, owner_id: str, *, project_id: Optional[str] = None
    ) -> bool:
        self.ensure_schema()
        where = "research_id = ? AND owner_id = ?"
        params: List[Any] = [research_id, owner_id]
        if project_id is not None:
            where += " AND project_id = ?"
            params.append(project_id)
        affected = self._db.execute(
            f"DELETE FROM project_research WHERE {where}", tuple(params)
        )
        return affected > 0

    # ── Internals ─────────────────────────────────────────────────────────

    @staticmethod
    def _project_filters(
        *,
        owner_id: str,
        status: Optional[str],
        research_domain: Optional[str],
        q: Optional[str],
        created_after: Optional[datetime],
        created_before: Optional[datetime],
    ) -> Tuple[str, Sequence[Any]]:
        where = ["owner_id = ?"]
        params: List[Any] = [owner_id]

        if status is not None:
            where.append("status = ?")
            params.append(_text(status))
        if research_domain:
            where.append("research_domain = ?")
            params.append(research_domain)
        if q:
            pattern = f"%{_escape_like(q)}%"
            where.append(
                "(name LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\')"
            )
            params.extend([pattern, pattern])
        if created_after is not None:
            where.append("created_at >= ?")
            params.append(utc_iso(created_after))
        if created_before is not None:
            where.append("created_at <= ?")
            params.append(utc_iso(created_before))
        return " AND ".join(where), params


__all__ = ["ProjectRepository", "utc_iso"]
