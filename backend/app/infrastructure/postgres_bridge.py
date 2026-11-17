"""
app/infrastructure/postgres_bridge.py
=====================================
Persistent PostgreSQL sync bridge for Supabase.

Ensures that research sessions, projects, documents, document chunks,
document chat conversations, and file bytes persist permanently across
Render container restarts and redeployments.
"""

from __future__ import annotations

import base64
import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from sqlalchemy import text
from app.config import get_settings
from app.observability import get_logger

logger = get_logger("app.infrastructure.postgres_bridge")

_INIT_LOCK = threading.Lock()
_SCHEMA_INITIALIZED = False


def _get_engine():
    try:
        from app.database.session import get_engine
        return get_engine()
    except Exception as exc:
        logger.warning(f"Could not initialize PostgreSQL engine: {exc}")
        return None


def init_postgres_schema() -> bool:
    """Create persistent tables in Supabase PostgreSQL if they do not exist."""
    global _SCHEMA_INITIALIZED
    if _SCHEMA_INITIALIZED:
        return True

    with _INIT_LOCK:
        if _SCHEMA_INITIALIZED:
            return True

        engine = _get_engine()
        if not engine:
            return False

        try:
            with engine.begin() as conn:
                # 1. Sessions table
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS app_sessions (
                        session_id          TEXT PRIMARY KEY,
                        topic               TEXT NOT NULL,
                        timestamp           TEXT NOT NULL,
                        provider            TEXT,
                        critic_score        INTEGER,
                        revision_count      INTEGER DEFAULT 0,
                        status              TEXT,
                        saved_report_path   TEXT,
                        docx_path           TEXT,
                        pdf_path            TEXT,
                        citations_verified  INTEGER DEFAULT 0,
                        citations_total     INTEGER DEFAULT 0,
                        duration_seconds    REAL,
                        notes               TEXT,
                        user_id             TEXT,
                        sources_json        TEXT,
                        claims_json         TEXT,
                        report_content      TEXT,
                        project_id          TEXT,
                        contradictions_json TEXT
                    );
                    CREATE INDEX IF NOT EXISTS ix_app_sessions_user ON app_sessions(user_id);
                    CREATE INDEX IF NOT EXISTS ix_app_sessions_time ON app_sessions(timestamp DESC);
                """))

                # 2. Projects table
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS app_projects (
                        project_id      TEXT PRIMARY KEY,
                        owner_id        TEXT NOT NULL,
                        name            TEXT NOT NULL,
                        description     TEXT,
                        research_domain TEXT,
                        status          TEXT NOT NULL,
                        created_at      TEXT NOT NULL,
                        updated_at      TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS ix_app_projects_owner ON app_projects(owner_id);
                """))

                # 3. Documents table
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS app_documents (
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
                        preview_text      TEXT
                    );
                    CREATE INDEX IF NOT EXISTS ix_app_documents_owner ON app_documents(owner_id);
                """))

                # 4. Document chunks table
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS app_document_chunks (
                        chunk_id    TEXT PRIMARY KEY,
                        document_id TEXT NOT NULL,
                        owner_id    TEXT NOT NULL,
                        project_id  TEXT,
                        chunk_index INTEGER NOT NULL,
                        page_number INTEGER DEFAULT 1,
                        section     TEXT,
                        content     TEXT NOT NULL,
                        created_at  TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS ix_app_chunks_doc ON app_document_chunks(document_id);
                    CREATE INDEX IF NOT EXISTS ix_app_chunks_owner ON app_document_chunks(owner_id);
                """))

                # 5. Document conversations & messages table
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS app_document_conversations (
                        conversation_id TEXT PRIMARY KEY,
                        owner_id        TEXT NOT NULL,
                        document_id     TEXT NOT NULL,
                        project_id      TEXT,
                        title           TEXT NOT NULL,
                        created_at      TEXT NOT NULL,
                        updated_at      TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS ix_app_doc_conv_owner ON app_document_conversations(owner_id, document_id);

                    CREATE TABLE IF NOT EXISTS app_document_chat_messages (
                        message_id      TEXT PRIMARY KEY,
                        conversation_id TEXT NOT NULL,
                        owner_id        TEXT NOT NULL,
                        role            TEXT NOT NULL,
                        content         TEXT NOT NULL,
                        citations_json  TEXT,
                        created_at      TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS ix_app_doc_msgs_conv ON app_document_chat_messages(conversation_id);
                """))

                # 6. Document binary file storage table
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS app_document_files (
                        storage_key TEXT PRIMARY KEY,
                        document_id TEXT NOT NULL,
                        owner_id    TEXT NOT NULL,
                        content     BYTEA NOT NULL,
                        created_at  TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS ix_app_files_doc ON app_document_files(document_id);
                """))

            _SCHEMA_INITIALIZED = True
            logger.info("postgres_bridge.schema_ready")
            return True
        except Exception as exc:
            logger.warning(f"Failed to initialize PostgreSQL schema: {exc}")
            return False


# ── SQLite Hydration on Startup / Cold Container ─────────────────────────────

def hydrate_sqlite_from_postgres(sqlite_conn: sqlite3.Connection, user_id: Optional[str] = None) -> None:
    """Copy records from Supabase PostgreSQL into the local SQLite database.
    
    This ensures that when Render restarts and SQLite is empty, all past
    sessions, projects, documents, conversations, and chunks are instantly restored.
    """
    engine = _get_engine()
    if not engine:
        return

    init_postgres_schema()

    try:
        with engine.connect() as pg_conn:
            # 1. Hydrate sessions
            session_sql = "SELECT * FROM app_sessions"
            params: Dict[str, Any] = {}
            if user_id:
                session_sql += " WHERE user_id = :user_id"
                params["user_id"] = user_id
            session_rows = pg_conn.execute(text(session_sql), params).mappings().fetchall()

            if session_rows:
                sqlite_conn.executemany(
                    """
                    INSERT OR IGNORE INTO research_sessions (
                        session_id, topic, timestamp, provider, critic_score,
                        revision_count, status, saved_report_path, docx_path, pdf_path,
                        citations_verified, citations_total, duration_seconds, notes, user_id,
                        sources_json, claims_json, report_content, project_id, contradictions_json
                    ) VALUES (
                        :session_id, :topic, :timestamp, :provider, :critic_score,
                        :revision_count, :status, :saved_report_path, :docx_path, :pdf_path,
                        :citations_verified, :citations_total, :duration_seconds, :notes, :user_id,
                        :sources_json, :claims_json, :report_content, :project_id, :contradictions_json
                    )
                    """,
                    [dict(r) for r in session_rows],
                )

            # 2. Hydrate projects
            proj_sql = "SELECT * FROM app_projects"
            if user_id:
                proj_sql += " WHERE owner_id = :user_id"
            proj_rows = pg_conn.execute(text(proj_sql), params).mappings().fetchall()

            if proj_rows:
                sqlite_conn.executemany(
                    """
                    INSERT OR IGNORE INTO projects (
                        project_id, owner_id, name, description, research_domain,
                        status, created_at, updated_at
                    ) VALUES (
                        :project_id, :owner_id, :name, :description, :research_domain,
                        :status, :created_at, :updated_at
                    )
                    """,
                    [dict(r) for r in proj_rows],
                )

            # 3. Hydrate documents
            doc_sql = "SELECT * FROM app_documents"
            if user_id:
                doc_sql += " WHERE owner_id = :user_id"
            doc_rows = pg_conn.execute(text(doc_sql), params).mappings().fetchall()

            if doc_rows:
                sqlite_conn.executemany(
                    """
                    INSERT OR IGNORE INTO documents (
                        document_id, owner_id, project_id, research_id, original_filename,
                        mime_type, size_bytes, sha256, storage_key, processing_status,
                        error_code, error_message, chunk_count, created_at, updated_at
                    ) VALUES (
                        :document_id, :owner_id, :project_id, :research_id, :original_filename,
                        :mime_type, :size_bytes, :sha256, :storage_key, :processing_status,
                        :error_code, :error_message, :chunk_count, :created_at, :updated_at
                    )
                    """,
                    [
                        {
                            "document_id": r["document_id"],
                            "owner_id": r["owner_id"],
                            "project_id": r.get("project_id"),
                            "research_id": r.get("research_id"),
                            "original_filename": r["original_filename"],
                            "mime_type": r["mime_type"],
                            "size_bytes": r["size_bytes"],
                            "sha256": r["sha256"],
                            "storage_key": r["storage_key"],
                            "processing_status": r["processing_status"],
                            "error_code": r.get("error_code"),
                            "error_message": r.get("error_message"),
                            "chunk_count": r.get("chunk_count", 0),
                            "created_at": r["created_at"],
                            "updated_at": r["updated_at"],
                        }
                        for r in doc_rows
                    ],
                )

            # 4. Hydrate conversations & messages
            conv_sql = "SELECT * FROM app_document_conversations"
            if user_id:
                conv_sql += " WHERE owner_id = :user_id"
            conv_rows = pg_conn.execute(text(conv_sql), params).mappings().fetchall()
            if conv_rows:
                sqlite_conn.executemany(
                    """
                    INSERT OR IGNORE INTO document_conversations (
                        conversation_id, owner_id, document_id, project_id, title,
                        created_at, updated_at
                    ) VALUES (
                        :conversation_id, :owner_id, :document_id, :project_id, :title,
                        :created_at, :updated_at
                    )
                    """,
                    [dict(r) for r in conv_rows],
                )

            msg_sql = "SELECT * FROM app_document_chat_messages"
            if user_id:
                msg_sql += " WHERE owner_id = :user_id"
            msg_rows = pg_conn.execute(text(msg_sql), params).mappings().fetchall()
            if msg_rows:
                sqlite_conn.executemany(
                    """
                    INSERT OR IGNORE INTO document_chat_messages (
                        message_id, conversation_id, owner_id, role, content,
                        citations_json, created_at
                    ) VALUES (
                        :message_id, :conversation_id, :owner_id, :role, :content,
                        :citations_json, :created_at
                    )
                    """,
                    [dict(r) for r in msg_rows],
                )

            # 5. Hydrate chunks
            chk_sql = "SELECT * FROM app_document_chunks"
            if user_id:
                chk_sql += " WHERE owner_id = :user_id"
            chk_rows = pg_conn.execute(text(chk_sql), params).mappings().fetchall()
            if chk_rows:
                try:
                    sqlite_conn.executemany(
                        """
                        INSERT OR IGNORE INTO document_chunks (
                            chunk_id, document_id, owner_id, project_id, chunk_index,
                            page_number, section, content, created_at
                        ) VALUES (
                            :chunk_id, :document_id, :owner_id, :project_id, :chunk_index,
                            :page_number, :section, :content, :created_at
                        )
                        """,
                        [dict(r) for r in chk_rows],
                    )
                except Exception:
                    pass

            sqlite_conn.commit()
            logger.info("postgres_bridge.hydration_complete", extra={"user_id": user_id})
    except Exception as exc:
        logger.warning(f"PostgreSQL hydration warning: {exc}")


# ── Sessions Persistence ─────────────────────────────────────────────────────

def persist_session(row: Dict[str, Any]) -> None:
    """Save or update a research session in Supabase PostgreSQL."""
    engine = _get_engine()
    if not engine:
        return

    init_postgres_schema()

    sql = """
    INSERT INTO app_sessions (
        session_id, topic, timestamp, provider, critic_score,
        revision_count, status, saved_report_path, docx_path, pdf_path,
        citations_verified, citations_total, duration_seconds, notes, user_id,
        sources_json, claims_json, report_content, project_id, contradictions_json
    ) VALUES (
        :session_id, :topic, :timestamp, :provider, :critic_score,
        :revision_count, :status, :saved_report_path, :docx_path, :pdf_path,
        :citations_verified, :citations_total, :duration_seconds, :notes, :user_id,
        :sources_json, :claims_json, :report_content, :project_id, :contradictions_json
    )
    ON CONFLICT (session_id) DO UPDATE SET
        topic = EXCLUDED.topic,
        timestamp = EXCLUDED.timestamp,
        provider = EXCLUDED.provider,
        critic_score = EXCLUDED.critic_score,
        revision_count = EXCLUDED.revision_count,
        status = EXCLUDED.status,
        saved_report_path = EXCLUDED.saved_report_path,
        docx_path = EXCLUDED.docx_path,
        pdf_path = EXCLUDED.pdf_path,
        citations_verified = EXCLUDED.citations_verified,
        citations_total = EXCLUDED.citations_total,
        duration_seconds = EXCLUDED.duration_seconds,
        notes = EXCLUDED.notes,
        user_id = EXCLUDED.user_id,
        sources_json = EXCLUDED.sources_json,
        claims_json = EXCLUDED.claims_json,
        report_content = EXCLUDED.report_content,
        project_id = EXCLUDED.project_id,
        contradictions_json = EXCLUDED.contradictions_json;
    """
    try:
        with engine.begin() as conn:
            conn.execute(text(sql), row)
    except Exception as exc:
        logger.warning(f"Failed to persist session to PostgreSQL: {exc}")


def delete_session(session_id: str, user_id: Optional[str] = None) -> None:
    engine = _get_engine()
    if not engine:
        return
    try:
        sql = "DELETE FROM app_sessions WHERE session_id = :session_id"
        params: Dict[str, Any] = {"session_id": session_id}
        if user_id:
            sql += " AND user_id = :user_id"
            params["user_id"] = user_id
        with engine.begin() as conn:
            conn.execute(text(sql), params)
    except Exception as exc:
        logger.warning(f"Failed to delete session from PostgreSQL: {exc}")


# ── Projects Persistence ─────────────────────────────────────────────────────

def persist_project(row: Dict[str, Any]) -> None:
    engine = _get_engine()
    if not engine:
        return
    init_postgres_schema()
    sql = """
    INSERT INTO app_projects (
        project_id, owner_id, name, description, research_domain,
        status, created_at, updated_at
    ) VALUES (
        :project_id, :owner_id, :name, :description, :research_domain,
        :status, :created_at, :updated_at
    )
    ON CONFLICT (project_id) DO UPDATE SET
        name = EXCLUDED.name,
        description = EXCLUDED.description,
        research_domain = EXCLUDED.research_domain,
        status = EXCLUDED.status,
        updated_at = EXCLUDED.updated_at;
    """
    try:
        with engine.begin() as conn:
            conn.execute(text(sql), row)
    except Exception as exc:
        logger.warning(f"Failed to persist project to PostgreSQL: {exc}")


def delete_project(project_id: str, owner_id: str) -> None:
    engine = _get_engine()
    if not engine:
        return
    try:
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM app_projects WHERE project_id = :pid AND owner_id = :oid"),
                {"pid": project_id, "oid": owner_id},
            )
    except Exception as exc:
        logger.warning(f"Failed to delete project from PostgreSQL: {exc}")


# ── Documents Persistence ────────────────────────────────────────────────────

def persist_document(row: Dict[str, Any]) -> None:
    engine = _get_engine()
    if not engine:
        return
    init_postgres_schema()
    sql = """
    INSERT INTO app_documents (
        document_id, owner_id, project_id, research_id, original_filename,
        mime_type, size_bytes, sha256, storage_key, processing_status,
        error_code, error_message, chunk_count, created_at, updated_at, preview_text
    ) VALUES (
        :document_id, :owner_id, :project_id, :research_id, :original_filename,
        :mime_type, :size_bytes, :sha256, :storage_key, :processing_status,
        :error_code, :error_message, :chunk_count, :created_at, :updated_at, :preview_text
    )
    ON CONFLICT (document_id) DO UPDATE SET
        project_id = EXCLUDED.project_id,
        processing_status = EXCLUDED.processing_status,
        error_code = EXCLUDED.error_code,
        error_message = EXCLUDED.error_message,
        chunk_count = EXCLUDED.chunk_count,
        updated_at = EXCLUDED.updated_at,
        preview_text = COALESCE(EXCLUDED.preview_text, app_documents.preview_text);
    """
    params = dict(row)
    params.setdefault("preview_text", None)
    try:
        with engine.begin() as conn:
            conn.execute(text(sql), params)
    except Exception as exc:
        logger.warning(f"Failed to persist document to PostgreSQL: {exc}")


def delete_document(document_id: str, owner_id: str) -> None:
    engine = _get_engine()
    if not engine:
        return
    try:
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM app_documents WHERE document_id = :did AND owner_id = :oid"),
                {"did": document_id, "oid": owner_id},
            )
            conn.execute(
                text("DELETE FROM app_document_chunks WHERE document_id = :did AND owner_id = :oid"),
                {"did": document_id, "oid": owner_id},
            )
            conn.execute(
                text("DELETE FROM app_document_files WHERE document_id = :did AND owner_id = :oid"),
                {"did": document_id, "oid": owner_id},
            )
            conn.execute(
                text("DELETE FROM app_document_conversations WHERE document_id = :did AND owner_id = :oid"),
                {"did": document_id, "oid": owner_id},
            )
    except Exception as exc:
        logger.warning(f"Failed to delete document from PostgreSQL: {exc}")


def persist_document_file(storage_key: str, document_id: str, owner_id: str, content: bytes) -> None:
    """Store raw document bytes in PostgreSQL for durability across ephemeral containers."""
    engine = _get_engine()
    if not engine:
        return
    init_postgres_schema()
    sql = """
    INSERT INTO app_document_files (storage_key, document_id, owner_id, content, created_at)
    VALUES (:storage_key, :document_id, :owner_id, :content, :created_at)
    ON CONFLICT (storage_key) DO UPDATE SET content = EXCLUDED.content;
    """
    now = datetime.now(timezone.utc).isoformat()
    try:
        with engine.begin() as conn:
            conn.execute(
                text(sql),
                {
                    "storage_key": storage_key,
                    "document_id": document_id,
                    "owner_id": owner_id,
                    "content": content,
                    "created_at": now,
                },
            )
    except Exception as exc:
        logger.warning(f"Failed to persist document file to PostgreSQL: {exc}")


def get_document_file(storage_key: str) -> Optional[bytes]:
    """Retrieve raw document bytes from Supabase PostgreSQL if missing from local disk."""
    engine = _get_engine()
    if not engine:
        return None
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT content FROM app_document_files WHERE storage_key = :k"),
                {"k": storage_key},
            ).fetchone()
            if row and row[0]:
                return bytes(row[0])
    except Exception as exc:
        logger.warning(f"Failed to fetch document file from PostgreSQL: {exc}")
    return None


def persist_document_chunks(
    document_id: str,
    owner_id: str,
    project_id: Optional[str],
    chunks: Sequence[Dict[str, Any]],
) -> None:
    engine = _get_engine()
    if not engine or not chunks:
        return
    init_postgres_schema()
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for c in chunks:
        cid = c.get("chunk_id") or f"{document_id}_{c.get('chunk_index', 0)}"
        rows.append({
            "chunk_id": cid,
            "document_id": document_id,
            "owner_id": owner_id,
            "project_id": project_id,
            "chunk_index": c.get("chunk_index", 0),
            "page_number": c.get("page_number", 1),
            "section": c.get("section", "Document Body"),
            "content": c.get("text", "") or c.get("content", ""),
            "created_at": now,
        })
    sql = """
    INSERT INTO app_document_chunks (
        chunk_id, document_id, owner_id, project_id, chunk_index,
        page_number, section, content, created_at
    ) VALUES (
        :chunk_id, :document_id, :owner_id, :project_id, :chunk_index,
        :page_number, :section, :content, :created_at
    )
    ON CONFLICT (chunk_id) DO UPDATE SET content = EXCLUDED.content;
    """
    try:
        with engine.begin() as conn:
            conn.execute(text(sql), rows)
    except Exception as exc:
        logger.warning(f"Failed to persist document chunks to PostgreSQL: {exc}")


def get_document_chunks(document_id: str, owner_id: str) -> List[Dict[str, Any]]:
    engine = _get_engine()
    if not engine:
        return []
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT * FROM app_document_chunks WHERE document_id = :did AND owner_id = :oid "
                    "ORDER BY chunk_index ASC"
                ),
                {"did": document_id, "oid": owner_id},
            ).mappings().fetchall()
            return [dict(r) for r in rows]
    except Exception as exc:
        logger.warning(f"Failed to fetch document chunks from PostgreSQL: {exc}")
        return []


# ── Conversations Persistence ────────────────────────────────────────────────

def persist_conversation(row: Dict[str, Any]) -> None:
    engine = _get_engine()
    if not engine:
        return
    init_postgres_schema()
    sql = """
    INSERT INTO app_document_conversations (
        conversation_id, owner_id, document_id, project_id, title, created_at, updated_at
    ) VALUES (
        :conversation_id, :owner_id, :document_id, :project_id, :title, :created_at, :updated_at
    )
    ON CONFLICT (conversation_id) DO UPDATE SET title = EXCLUDED.title, updated_at = EXCLUDED.updated_at;
    """
    try:
        with engine.begin() as conn:
            conn.execute(text(sql), row)
    except Exception as exc:
        logger.warning(f"Failed to persist conversation to PostgreSQL: {exc}")


def persist_message(row: Dict[str, Any]) -> None:
    engine = _get_engine()
    if not engine:
        return
    init_postgres_schema()
    sql = """
    INSERT INTO app_document_chat_messages (
        message_id, conversation_id, owner_id, role, content, citations_json, created_at
    ) VALUES (
        :message_id, :conversation_id, :owner_id, :role, :content, :citations_json, :created_at
    )
    ON CONFLICT (message_id) DO NOTHING;
    """
    try:
        with engine.begin() as conn:
            conn.execute(text(sql), row)
    except Exception as exc:
        logger.warning(f"Failed to persist chat message to PostgreSQL: {exc}")


# ── Knowledge Base Aggregation ───────────────────────────────────────────────

def get_knowledge_stats(user_id: str) -> Dict[str, int]:
    """Calculate aggregated knowledge statistics directly from PostgreSQL."""
    engine = _get_engine()
    if not engine:
        return {"total_documents": 0, "total_chunks": 0, "total_memories": 0, "contributing_sessions": 0}

    init_postgres_schema()
    try:
        with engine.connect() as conn:
            # Count indexed documents
            doc_cnt = conn.execute(
                text("SELECT count(*) FROM app_documents WHERE owner_id = :uid"),
                {"uid": user_id},
            ).scalar() or 0

            # Count vectorized chunks
            chunk_cnt = conn.execute(
                text("SELECT count(*) FROM app_document_chunks WHERE owner_id = :uid"),
                {"uid": user_id},
            ).scalar() or 0

            # If chunks table is 0, sum chunk_count from app_documents
            if chunk_cnt == 0:
                chunk_sum = conn.execute(
                    text("SELECT COALESCE(SUM(chunk_count), 0) FROM app_documents WHERE owner_id = :uid"),
                    {"uid": user_id},
                ).scalar() or 0
                chunk_cnt = int(chunk_sum)

            # Count research sessions
            session_cnt = conn.execute(
                text("SELECT count(*) FROM app_sessions WHERE user_id = :uid"),
                {"uid": user_id},
            ).scalar() or 0

            # Count research memories
            import uuid as _uuid
            try:
                u_uuid = _uuid.UUID(str(user_id))
                mem_cnt = conn.execute(
                    text("SELECT count(*) FROM research_memory WHERE user_id = :uid"),
                    {"uid": u_uuid},
                ).scalar() or 0
            except Exception:
                mem_cnt = 0

            return {
                "total_documents": int(doc_cnt),
                "total_chunks": int(chunk_cnt),
                "total_memories": int(mem_cnt),
                "contributing_sessions": int(session_cnt),
            }
    except Exception as exc:
        logger.warning(f"Failed to query PostgreSQL knowledge stats: {exc}")
        return {"total_documents": 0, "total_chunks": 0, "total_memories": 0, "contributing_sessions": 0}


def list_knowledge_documents(user_id: str, limit: int = 100) -> List[Dict[str, Any]]:
    """List documents for the Knowledge Base dashboard."""
    engine = _get_engine()
    if not engine:
        return []
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT * FROM app_documents WHERE owner_id = :uid "
                    "ORDER BY created_at DESC LIMIT :lim"
                ),
                {"uid": user_id, "lim": limit},
            ).mappings().fetchall()
            return [dict(r) for r in rows]
    except Exception as exc:
        logger.warning(f"Failed to list knowledge documents from PostgreSQL: {exc}")
        return []
