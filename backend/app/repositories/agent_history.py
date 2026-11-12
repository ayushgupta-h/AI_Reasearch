"""
app/repositories/agent_history.py
================================
SQL persistence for agent executions and research timeline events.

Every row carries ``research_id`` and ``owner_id``; queries always filter on
both, so ownership is enforced at the storage boundary. Rows are written only
by :class:`app.services.agent_history_service.AgentExecutionRecorder` when a
pipeline stage actually runs — history is never synthesised by the API.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from app.infrastructure.ports import Database
from app.repositories.projects import utc_iso

_CREATE_RUNS = """
CREATE TABLE IF NOT EXISTS research_runs (
    research_id TEXT PRIMARY KEY,
    owner_id    TEXT NOT NULL,
    topic       TEXT,
    status      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
"""

_CREATE_EXECUTIONS = """
CREATE TABLE IF NOT EXISTS agent_executions (
    execution_id     TEXT PRIMARY KEY,
    research_id      TEXT NOT NULL,
    owner_id         TEXT NOT NULL,
    agent_name       TEXT NOT NULL,
    status           TEXT NOT NULL,
    started_at       TEXT NOT NULL,
    finished_at      TEXT,
    duration_ms      REAL,
    retry_count      INTEGER NOT NULL DEFAULT 0,
    error_summary    TEXT,
    output_reference TEXT
);
"""

_CREATE_EVENTS = """
CREATE TABLE IF NOT EXISTS research_timeline_events (
    sequence     INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id     TEXT NOT NULL UNIQUE,
    research_id  TEXT NOT NULL,
    owner_id     TEXT NOT NULL,
    event_type   TEXT NOT NULL,
    agent_name   TEXT,
    message      TEXT NOT NULL,
    execution_id TEXT,
    occurred_at  TEXT NOT NULL
);
"""

_CREATE_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_research_runs_owner "
    "ON research_runs(owner_id, updated_at);",
    "CREATE INDEX IF NOT EXISTS idx_agent_executions_research "
    "ON agent_executions(research_id, owner_id, started_at);",
    "CREATE INDEX IF NOT EXISTS idx_timeline_events_research "
    "ON research_timeline_events(research_id, owner_id, sequence);",
)

_EXECUTION_COLUMNS = (
    "execution_id, research_id, owner_id, agent_name, status, started_at, "
    "finished_at, duration_ms, retry_count, error_summary, output_reference"
)
_EVENT_COLUMNS = (
    "event_id, research_id, owner_id, event_type, agent_name, message, "
    "execution_id, occurred_at, sequence"
)

_EXECUTION_SORTS = {
    "started_at": "started_at",
    "agent_name": "agent_name",
    "status": "status",
    "duration_ms": "duration_ms",
}


class AgentHistoryRepository:
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
            self._db.execute(_CREATE_RUNS)
            self._db.execute(_CREATE_EXECUTIONS)
            self._db.execute(_CREATE_EVENTS)
            for statement in _CREATE_INDEXES:
                self._db.execute(statement)
            self._schema_ready = True

    # ── Research runs ─────────────────────────────────────────────────────

    def ensure_run(
        self,
        research_id: str,
        owner_id: str,
        *,
        topic: Optional[str] = None,
        status: str = "pending",
    ) -> None:
        self.ensure_schema()
        now = utc_iso()
        self._db.execute(
            """
            INSERT OR IGNORE INTO research_runs (
                research_id, owner_id, topic, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (research_id, owner_id, topic, status, now, now),
        )

    def get_run(self, research_id: str, owner_id: str) -> Optional[Dict[str, Any]]:
        self.ensure_schema()
        return self._db.fetch_one(
            "SELECT research_id, owner_id, topic, status, created_at, updated_at "
            "FROM research_runs WHERE research_id = ? AND owner_id = ?",
            (research_id, owner_id),
        )

    def update_run_status(self, research_id: str, owner_id: str, status: str) -> None:
        self.ensure_schema()
        self._db.execute(
            "UPDATE research_runs SET status = ?, updated_at = ? "
            "WHERE research_id = ? AND owner_id = ?",
            (status, utc_iso(), research_id, owner_id),
        )

    # ── Executions ────────────────────────────────────────────────────────

    def create_execution(
        self,
        *,
        research_id: str,
        owner_id: str,
        agent_name: str,
        status: str,
        retry_count: int = 0,
    ) -> Dict[str, Any]:
        self.ensure_schema()
        execution_id = uuid.uuid4().hex
        self._db.execute(
            """
            INSERT INTO agent_executions (
                execution_id, research_id, owner_id, agent_name, status,
                started_at, retry_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                execution_id,
                research_id,
                owner_id,
                agent_name,
                status,
                utc_iso(),
                retry_count,
            ),
        )
        row = self.get_execution(execution_id, research_id, owner_id)
        if row is None:  # pragma: no cover - insert just succeeded
            raise RuntimeError("Agent execution insert was not readable back")
        return row

    def finish_execution(
        self,
        execution_id: str,
        owner_id: str,
        *,
        status: str,
        duration_ms: Optional[float],
        error_summary: Optional[str] = None,
        output_reference: Optional[str] = None,
    ) -> None:
        self.ensure_schema()
        self._db.execute(
            """
            UPDATE agent_executions
               SET status = ?, finished_at = ?, duration_ms = ?,
                   error_summary = ?, output_reference = ?
             WHERE execution_id = ? AND owner_id = ?
            """,
            (
                status,
                utc_iso(),
                duration_ms,
                error_summary,
                output_reference,
                execution_id,
                owner_id,
            ),
        )

    def count_agent_executions(
        self, research_id: str, owner_id: str, agent_name: str
    ) -> int:
        self.ensure_schema()
        row = self._db.fetch_one(
            "SELECT COUNT(*) AS total FROM agent_executions "
            "WHERE research_id = ? AND owner_id = ? AND agent_name = ?",
            (research_id, owner_id, agent_name),
        )
        return int((row or {}).get("total", 0))

    def get_execution(
        self, execution_id: str, research_id: str, owner_id: str
    ) -> Optional[Dict[str, Any]]:
        self.ensure_schema()
        return self._db.fetch_one(
            f"SELECT {_EXECUTION_COLUMNS} FROM agent_executions "
            "WHERE execution_id = ? AND research_id = ? AND owner_id = ?",
            (execution_id, research_id, owner_id),
        )

    def list_executions(
        self,
        *,
        research_id: str,
        owner_id: str,
        status: Optional[str] = None,
        agent_name: Optional[str] = None,
        started_after: Optional[datetime] = None,
        started_before: Optional[datetime] = None,
        sort: str = "started_at",
        order: str = "asc",
        limit: int = 20,
        offset: int = 0,
    ) -> Tuple[List[Dict[str, Any]], int]:
        self.ensure_schema()
        where = ["research_id = ?", "owner_id = ?"]
        params: List[Any] = [research_id, owner_id]

        if status is not None:
            where.append("status = ?")
            params.append(getattr(status, "value", str(status)))
        if agent_name:
            where.append("agent_name = ?")
            params.append(agent_name)
        if started_after is not None:
            where.append("started_at >= ?")
            params.append(utc_iso(started_after))
        if started_before is not None:
            where.append("started_at <= ?")
            params.append(utc_iso(started_before))

        clause = " AND ".join(where)
        column = _EXECUTION_SORTS.get(sort, "started_at")
        direction = "ASC" if order.lower() == "asc" else "DESC"

        total_row = self._db.fetch_one(
            f"SELECT COUNT(*) AS total FROM agent_executions WHERE {clause}", params
        )
        total = int((total_row or {}).get("total", 0))
        rows = self._db.fetch_all(
            f"SELECT {_EXECUTION_COLUMNS} FROM agent_executions WHERE {clause} "
            f"ORDER BY {column} {direction}, execution_id ASC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )
        return rows, total

    def execution_counts(self, research_id: str, owner_id: str) -> Dict[str, int]:
        self.ensure_schema()
        rows = self._db.fetch_all(
            "SELECT status, COUNT(*) AS total FROM agent_executions "
            "WHERE research_id = ? AND owner_id = ? GROUP BY status",
            (research_id, owner_id),
        )
        return {str(row["status"]): int(row["total"]) for row in rows}

    def current_agent(self, research_id: str, owner_id: str) -> Optional[str]:
        self.ensure_schema()
        row = self._db.fetch_one(
            "SELECT agent_name FROM agent_executions "
            "WHERE research_id = ? AND owner_id = ? AND status = 'running' "
            "ORDER BY started_at DESC LIMIT 1",
            (research_id, owner_id),
        )
        return str(row["agent_name"]) if row else None

    # ── Timeline events ───────────────────────────────────────────────────

    def record_event(
        self,
        *,
        research_id: str,
        owner_id: str,
        event_type: str,
        message: str,
        agent_name: Optional[str] = None,
        execution_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.ensure_schema()
        event_id = uuid.uuid4().hex
        occurred_at = utc_iso()
        self._db.execute(
            """
            INSERT INTO research_timeline_events (
                event_id, research_id, owner_id, event_type, agent_name,
                message, execution_id, occurred_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                research_id,
                owner_id,
                getattr(event_type, "value", str(event_type)),
                agent_name,
                message,
                execution_id,
                occurred_at,
            ),
        )
        row = self._db.fetch_one(
            f"SELECT {_EVENT_COLUMNS} FROM research_timeline_events "
            "WHERE event_id = ?",
            (event_id,),
        )
        if row is None:  # pragma: no cover
            raise RuntimeError("Timeline event insert was not readable back")
        return row

    def list_events(
        self,
        *,
        research_id: str,
        owner_id: str,
        event_type: Optional[str] = None,
        agent_name: Optional[str] = None,
        occurred_after: Optional[datetime] = None,
        occurred_before: Optional[datetime] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[Dict[str, Any]], int]:
        self.ensure_schema()
        where = ["research_id = ?", "owner_id = ?"]
        params: List[Any] = [research_id, owner_id]

        if event_type is not None:
            where.append("event_type = ?")
            params.append(getattr(event_type, "value", str(event_type)))
        if agent_name:
            where.append("agent_name = ?")
            params.append(agent_name)
        if occurred_after is not None:
            where.append("occurred_at >= ?")
            params.append(utc_iso(occurred_after))
        if occurred_before is not None:
            where.append("occurred_at <= ?")
            params.append(utc_iso(occurred_before))

        clause = " AND ".join(where)
        total_row = self._db.fetch_one(
            f"SELECT COUNT(*) AS total FROM research_timeline_events WHERE {clause}",
            params,
        )
        total = int((total_row or {}).get("total", 0))
        rows = self._db.fetch_all(
            f"SELECT {_EVENT_COLUMNS} FROM research_timeline_events WHERE {clause} "
            "ORDER BY sequence ASC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )
        return rows, total

    def latest_event(
        self, research_id: str, owner_id: str
    ) -> Optional[Mapping[str, Any]]:
        self.ensure_schema()
        return self._db.fetch_one(
            f"SELECT {_EVENT_COLUMNS} FROM research_timeline_events "
            "WHERE research_id = ? AND owner_id = ? "
            "ORDER BY sequence DESC LIMIT 1",
            (research_id, owner_id),
        )


__all__ = ["AgentHistoryRepository"]
