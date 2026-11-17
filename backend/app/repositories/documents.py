"""
app/repositories/documents.py
=============================
SQL persistence for uploaded research documents.

Metadata lives in the application database; bytes live behind the
``ObjectStorage`` port. Every query is scoped by ``owner_id``.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.infrastructure.ports import Database
from app.repositories.projects import utc_iso

_CREATE_DOCUMENTS = """
CREATE TABLE IF NOT EXISTS documents (
    document_id       TEXT PRIMARY KEY,
    owner_id          TEXT NOT NULL,
    project_id        TEXT,
    research_id       TEXT,
    original_filename TEXT NOT NULL,
    mime_type         TEXT NOT NULL,
    size_bytes        INTEGER NOT NULL,
    sha256            TEXT NOT NULL,
    storage_key       TEXT NOT NULL,
    processing_status TEXT NOT NULL,
    error_code        TEXT,
    error_message     TEXT,
    chunk_count       INTEGER DEFAULT 0,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    UNIQUE(owner_id, sha256)
);
"""

_CREATE_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_documents_owner "
    "ON documents(owner_id, created_at);"
)

_CREATE_CONVERSATIONS = """
CREATE TABLE IF NOT EXISTS document_conversations (
    conversation_id   TEXT PRIMARY KEY,
    owner_id          TEXT NOT NULL,
    document_id       TEXT NOT NULL,
    project_id        TEXT,
    title             TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);
"""

_CREATE_MESSAGES = """
CREATE TABLE IF NOT EXISTS document_chat_messages (
    message_id        TEXT PRIMARY KEY,
    conversation_id   TEXT NOT NULL,
    owner_id          TEXT NOT NULL,
    role              TEXT NOT NULL,
    content           TEXT NOT NULL,
    citations_json    TEXT,
    created_at        TEXT NOT NULL
);
"""

_COLUMNS = (
    "document_id, owner_id, project_id, research_id, original_filename, "
    "mime_type, size_bytes, sha256, storage_key, processing_status, "
    "error_code, error_message, chunk_count, created_at, updated_at"
)

_SORTS = {
    "created_at": "created_at",
    "original_filename": "original_filename",
    "size_bytes": "size_bytes",
    "processing_status": "processing_status",
}


class DocumentRepository:
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
            self._db.execute(_CREATE_DOCUMENTS)
            self._db.execute(_CREATE_INDEX)
            self._db.execute(_CREATE_CONVERSATIONS)
            self._db.execute(_CREATE_MESSAGES)
            try:
                info = self._db.fetch_all("PRAGMA table_info(documents)")
                cols = {
                    (r["name"] if isinstance(r, dict) else r[1])
                    for r in info
                }
                if cols and "chunk_count" not in cols:
                    self._db.execute("ALTER TABLE documents ADD COLUMN chunk_count INTEGER DEFAULT 0")
            except Exception:
                try:
                    self._db.execute("ALTER TABLE documents ADD COLUMN chunk_count INTEGER DEFAULT 0")
                except Exception:
                    pass
            self._schema_ready = True

    def create(
        self,
        *,
        owner_id: str,
        project_id: Optional[str],
        research_id: Optional[str],
        original_filename: str,
        mime_type: str,
        size_bytes: int,
        sha256: str,
        storage_key: str,
        processing_status: str,
    ) -> Dict[str, Any]:
        self.ensure_schema()
        document_id = uuid.uuid4().hex
        now = utc_iso()
        self._db.execute(
            """
            INSERT INTO documents (
                document_id, owner_id, project_id, research_id, original_filename,
                mime_type, size_bytes, sha256, storage_key, processing_status,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document_id,
                owner_id,
                project_id,
                research_id,
                original_filename,
                mime_type,
                size_bytes,
                sha256,
                storage_key,
                processing_status,
                now,
                now,
            ),
        )
        row = self.get(document_id, owner_id)
        if row is None:  # pragma: no cover - insert just succeeded
            raise RuntimeError("Document insert was not readable back")
        return row

    def get(self, document_id: str, owner_id: str) -> Optional[Dict[str, Any]]:
        self.ensure_schema()
        return self._db.fetch_one(
            f"SELECT {_COLUMNS} FROM documents "
            "WHERE document_id = ? AND owner_id = ?",
            (document_id, owner_id),
        )

    def find_by_hash(self, owner_id: str, sha256: str) -> Optional[Dict[str, Any]]:
        self.ensure_schema()
        return self._db.fetch_one(
            f"SELECT {_COLUMNS} FROM documents "
            "WHERE owner_id = ? AND sha256 = ?",
            (owner_id, sha256),
        )

    def list_documents(
        self,
        *,
        owner_id: str,
        project_id: Optional[str] = None,
        research_id: Optional[str] = None,
        status: Optional[str] = None,
        q: Optional[str] = None,
        created_after: Optional[datetime] = None,
        created_before: Optional[datetime] = None,
        sort: str = "created_at",
        order: str = "desc",
        limit: int = 20,
        offset: int = 0,
    ) -> Tuple[List[Dict[str, Any]], int]:
        self.ensure_schema()
        where = ["owner_id = ?"]
        params: List[Any] = [owner_id]

        if project_id is not None:
            where.append("project_id = ?")
            params.append(project_id)
        if research_id is not None:
            where.append("research_id = ?")
            params.append(research_id)
        if status is not None:
            where.append("processing_status = ?")
            params.append(getattr(status, "value", str(status)))
        if q:
            escaped = (
                q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            where.append("original_filename LIKE ? ESCAPE '\\'")
            params.append(f"%{escaped}%")
        if created_after is not None:
            where.append("created_at >= ?")
            params.append(utc_iso(created_after))
        if created_before is not None:
            where.append("created_at <= ?")
            params.append(utc_iso(created_before))

        clause = " AND ".join(where)
        column = _SORTS.get(sort, "created_at")
        direction = "ASC" if order.lower() == "asc" else "DESC"

        total_row = self._db.fetch_one(
            f"SELECT COUNT(*) AS total FROM documents WHERE {clause}", params
        )
        total = int((total_row or {}).get("total", 0))
        rows = self._db.fetch_all(
            f"SELECT {_COLUMNS} FROM documents WHERE {clause} "
            f"ORDER BY {column} {direction}, document_id ASC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )
        return rows, total

    def update_status(
        self,
        document_id: str,
        owner_id: str,
        *,
        status: str,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        self.ensure_schema()
        self._db.execute(
            "UPDATE documents SET processing_status = ?, error_code = ?, "
            "error_message = ?, updated_at = ? "
            "WHERE document_id = ? AND owner_id = ?",
            (
                getattr(status, "value", str(status)),
                error_code,
                error_message,
                utc_iso(),
                document_id,
                owner_id,
            ),
        )

    def update_chunk_count(self, document_id: str, owner_id: str, chunk_count: int) -> None:
        self.ensure_schema()
        self._db.execute(
            "UPDATE documents SET chunk_count = ?, updated_at = ? WHERE document_id = ? AND owner_id = ?",
            (chunk_count, utc_iso(), document_id, owner_id),
        )

    def delete(self, document_id: str, owner_id: str) -> bool:
        self.ensure_schema()
        affected = self._db.execute(
            "DELETE FROM documents WHERE document_id = ? AND owner_id = ?",
            (document_id, owner_id),
        )
        return affected > 0

    # ── Conversational Document Chat ───────────────────────────────────────

    def create_conversation(
        self,
        owner_id: str,
        document_id: str,
        title: str = "Document Chat",
        project_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.ensure_schema()
        conversation_id = uuid.uuid4().hex
        now = utc_iso()
        self._db.execute(
            "INSERT INTO document_conversations "
            "(conversation_id, owner_id, document_id, project_id, title, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (conversation_id, owner_id, document_id, project_id, title, now, now),
        )
        return {
            "conversation_id": conversation_id,
            "owner_id": owner_id,
            "document_id": document_id,
            "project_id": project_id,
            "title": title,
            "created_at": now,
            "updated_at": now,
        }

    def list_conversations(
        self,
        owner_id: str,
        document_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        self.ensure_schema()
        if document_id:
            rows = self._db.fetch_all(
                "SELECT conversation_id, owner_id, document_id, project_id, title, created_at, updated_at "
                "FROM document_conversations WHERE owner_id = ? AND document_id = ? "
                "ORDER BY updated_at DESC",
                (owner_id, document_id),
            )
        else:
            rows = self._db.fetch_all(
                "SELECT conversation_id, owner_id, document_id, project_id, title, created_at, updated_at "
                "FROM document_conversations WHERE owner_id = ? "
                "ORDER BY updated_at DESC",
                (owner_id,),
            )
        return [dict(r) for r in rows]

    def get_conversation(self, conversation_id: str, owner_id: str) -> Optional[Dict[str, Any]]:
        self.ensure_schema()
        row = self._db.fetch_one(
            "SELECT conversation_id, owner_id, document_id, project_id, title, created_at, updated_at "
            "FROM document_conversations WHERE conversation_id = ? AND owner_id = ?",
            (conversation_id, owner_id),
        )
        return dict(row) if row else None

    def delete_conversation(self, conversation_id: str, owner_id: str) -> bool:
        self.ensure_schema()
        self._db.execute(
            "DELETE FROM document_chat_messages WHERE conversation_id = ? AND owner_id = ?",
            (conversation_id, owner_id),
        )
        affected = self._db.execute(
            "DELETE FROM document_conversations WHERE conversation_id = ? AND owner_id = ?",
            (conversation_id, owner_id),
        )
        return affected > 0

    def add_message(
        self,
        conversation_id: str,
        owner_id: str,
        role: str,
        content: str,
        citations_json: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.ensure_schema()
        message_id = uuid.uuid4().hex
        now = utc_iso()
        self._db.execute(
            "INSERT INTO document_chat_messages "
            "(message_id, conversation_id, owner_id, role, content, citations_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (message_id, conversation_id, owner_id, role, content, citations_json, now),
        )
        self._db.execute(
            "UPDATE document_conversations SET updated_at = ? WHERE conversation_id = ? AND owner_id = ?",
            (now, conversation_id, owner_id),
        )
        return {
            "message_id": message_id,
            "conversation_id": conversation_id,
            "owner_id": owner_id,
            "role": role,
            "content": content,
            "citations_json": citations_json,
            "created_at": now,
        }

    def get_messages(
        self,
        conversation_id: str,
        owner_id: str,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        self.ensure_schema()
        rows = self._db.fetch_all(
            "SELECT message_id, conversation_id, owner_id, role, content, citations_json, created_at "
            "FROM document_chat_messages WHERE conversation_id = ? AND owner_id = ? "
            "ORDER BY created_at ASC LIMIT ?",
            (conversation_id, owner_id, limit),
        )
        return [dict(r) for r in rows]


__all__ = ["DocumentRepository"]
