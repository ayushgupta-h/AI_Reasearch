"""
app/repositories/reports.py
===========================
SQL persistence for report metadata and immutable report versions.

Every read and write is scoped by ``owner_id``; the API never trusts a
client-supplied owner.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional, Tuple

from app.infrastructure.ports import Database
from app.repositories.projects import utc_iso

_CREATE_REPORTS = """
CREATE TABLE IF NOT EXISTS research_reports (
    report_id           TEXT PRIMARY KEY,
    research_id         TEXT NOT NULL,
    owner_id            TEXT NOT NULL,
    title               TEXT NOT NULL,
    format              TEXT NOT NULL,
    version             INTEGER NOT NULL,
    status              TEXT NOT NULL,
    critic_score        INTEGER,
    verification_status TEXT NOT NULL,
    storage_reference   TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
"""

_CREATE_REPORT_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_research_reports_owner "
    "ON research_reports(owner_id, research_id, created_at);"
)

_CREATE_VERSIONS = """
CREATE TABLE IF NOT EXISTS report_versions (
    version_id          TEXT PRIMARY KEY,
    report_id           TEXT NOT NULL,
    owner_id            TEXT NOT NULL,
    version             INTEGER NOT NULL,
    status              TEXT NOT NULL,
    content_markdown    TEXT NOT NULL,
    critic_score        INTEGER,
    verification_status TEXT NOT NULL,
    storage_reference   TEXT,
    change_summary      TEXT,
    created_at          TEXT NOT NULL,
    UNIQUE(report_id, version)
);
"""

_REPORT_COLUMNS = (
    "report_id, research_id, owner_id, title, format, version, status, "
    "critic_score, verification_status, storage_reference, created_at, updated_at"
)

_VERSION_COLUMNS = (
    "version_id, report_id, owner_id, version, status, content_markdown, "
    "critic_score, verification_status, storage_reference, change_summary, created_at"
)

_REPORT_SORTS = {
    "created_at": "created_at",
    "updated_at": "updated_at",
    "title": "title",
    "status": "status",
    "version": "version",
}

_UPDATABLE = {
    "title",
    "format",
    "version",
    "status",
    "critic_score",
    "verification_status",
    "storage_reference",
}


class ReportRepository:
    def __init__(self, database: Database) -> None:
        self._db = database
        self._schema_ready = False
        self._schema_lock = threading.Lock()

    def ensure_schema(self) -> None:
        if self._schema_ready:
            return
        with self._schema_lock:
            if self._schema_ready:
                return
            self._db.execute(_CREATE_REPORTS)
            self._db.execute(_CREATE_REPORT_INDEX)
            self._db.execute(_CREATE_VERSIONS)
            self._schema_ready = True

    # ── Reports ───────────────────────────────────────────────────────────

    def create_report(
        self,
        *,
        report_id: str,
        research_id: str,
        owner_id: str,
        title: str,
        format: str,
        version: int,
        status: str,
        critic_score: Optional[int],
        verification_status: str,
        storage_reference: Optional[str],
    ) -> Dict[str, Any]:
        self.ensure_schema()
        now = utc_iso()
        self._db.execute(
            """
            INSERT INTO research_reports (
                report_id, research_id, owner_id, title, format, version, status,
                critic_score, verification_status, storage_reference,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report_id,
                research_id,
                owner_id,
                title,
                format,
                version,
                status,
                critic_score,
                verification_status,
                storage_reference,
                now,
                now,
            ),
        )
        row = self.get_report(report_id, owner_id)
        if row is None:  # pragma: no cover
            raise RuntimeError("Report insert was not readable back")
        return row

    def get_report(
        self, report_id: str, owner_id: str
    ) -> Optional[Dict[str, Any]]:
        self.ensure_schema()
        return self._db.fetch_one(
            f"SELECT {_REPORT_COLUMNS} FROM research_reports "
            "WHERE report_id = ? AND owner_id = ?",
            (report_id, owner_id),
        )

    def list_reports(
        self,
        *,
        research_id: str,
        owner_id: str,
        status: Optional[str] = None,
        sort: str = "created_at",
        order: str = "desc",
        limit: int = 20,
        offset: int = 0,
    ) -> Tuple[List[Dict[str, Any]], int]:
        self.ensure_schema()
        where = ["research_id = ?", "owner_id = ?"]
        params: List[Any] = [research_id, owner_id]
        if status is not None:
            where.append("status = ?")
            params.append(getattr(status, "value", str(status)))
        clause = " AND ".join(where)
        column = _REPORT_SORTS.get(sort, "created_at")
        direction = "ASC" if order.lower() == "asc" else "DESC"

        total_row = self._db.fetch_one(
            f"SELECT COUNT(*) AS total FROM research_reports WHERE {clause}",
            params,
        )
        total = int((total_row or {}).get("total", 0))
        rows = self._db.fetch_all(
            f"SELECT {_REPORT_COLUMNS} FROM research_reports WHERE {clause} "
            f"ORDER BY {column} {direction}, report_id ASC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )
        return rows, total

    def update_report(
        self, report_id: str, owner_id: str, changes: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        self.ensure_schema()
        allowed = {key: value for key, value in changes.items() if key in _UPDATABLE}
        if not allowed:
            return self.get_report(report_id, owner_id)
        allowed["updated_at"] = utc_iso()
        assignments = ", ".join(f"{column} = ?" for column in allowed)
        values = [getattr(value, "value", value) for value in allowed.values()]
        self._db.execute(
            f"UPDATE research_reports SET {assignments} "
            "WHERE report_id = ? AND owner_id = ?",
            (*values, report_id, owner_id),
        )
        return self.get_report(report_id, owner_id)

    def delete_report(self, report_id: str, owner_id: str) -> bool:
        self.ensure_schema()
        self._db.execute(
            "DELETE FROM report_versions WHERE report_id = ? AND owner_id = ?",
            (report_id, owner_id),
        )
        affected = self._db.execute(
            "DELETE FROM research_reports WHERE report_id = ? AND owner_id = ?",
            (report_id, owner_id),
        )
        return affected > 0

    # ── Versions ──────────────────────────────────────────────────────────

    def create_version(
        self,
        *,
        version_id: str,
        report_id: str,
        owner_id: str,
        version: int,
        status: str,
        content_markdown: str,
        critic_score: Optional[int],
        verification_status: str,
        storage_reference: Optional[str],
        change_summary: Optional[str],
    ) -> Dict[str, Any]:
        self.ensure_schema()
        self._db.execute(
            """
            INSERT INTO report_versions (
                version_id, report_id, owner_id, version, status,
                content_markdown, critic_score, verification_status,
                storage_reference, change_summary, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                version_id,
                report_id,
                owner_id,
                version,
                status,
                content_markdown,
                critic_score,
                verification_status,
                storage_reference,
                change_summary,
                utc_iso(),
            ),
        )
        row = self.get_version(version_id, owner_id)
        if row is None:  # pragma: no cover
            raise RuntimeError("Report version insert was not readable back")
        return row

    def get_version(
        self, version_id: str, owner_id: str
    ) -> Optional[Dict[str, Any]]:
        self.ensure_schema()
        return self._db.fetch_one(
            f"SELECT {_VERSION_COLUMNS} FROM report_versions "
            "WHERE version_id = ? AND owner_id = ?",
            (version_id, owner_id),
        )

    def get_version_by_number(
        self, report_id: str, version: int, owner_id: str
    ) -> Optional[Dict[str, Any]]:
        self.ensure_schema()
        return self._db.fetch_one(
            f"SELECT {_VERSION_COLUMNS} FROM report_versions "
            "WHERE report_id = ? AND version = ? AND owner_id = ?",
            (report_id, version, owner_id),
        )

    def list_versions(
        self, report_id: str, owner_id: str
    ) -> List[Dict[str, Any]]:
        self.ensure_schema()
        return self._db.fetch_all(
            f"SELECT {_VERSION_COLUMNS} FROM report_versions "
            "WHERE report_id = ? AND owner_id = ? ORDER BY version DESC",
            (report_id, owner_id),
        )

    def delete_version(self, version_id: str, owner_id: str) -> None:
        self.ensure_schema()
        self._db.execute(
            "DELETE FROM report_versions WHERE version_id = ? AND owner_id = ?",
            (version_id, owner_id),
        )


__all__ = ["ReportRepository"]
