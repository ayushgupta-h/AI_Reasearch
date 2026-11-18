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

                # 7. Report comparisons & messages table
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS app_report_comparisons (
                        comparison_id    TEXT PRIMARY KEY,
                        owner_id         TEXT NOT NULL,
                        title            TEXT NOT NULL,
                        report_a_id      TEXT NOT NULL,
                        report_a_title   TEXT NOT NULL,
                        report_a_type    TEXT NOT NULL,
                        report_b_id      TEXT NOT NULL,
                        report_b_title   TEXT NOT NULL,
                        report_b_type    TEXT NOT NULL,
                        status           TEXT NOT NULL,
                        summary_json     TEXT,
                        analysis_json    TEXT,
                        sections_json    TEXT,
                        differences_json TEXT,
                        created_at       TEXT NOT NULL,
                        updated_at       TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS ix_app_comp_owner ON app_report_comparisons(owner_id);
                    CREATE INDEX IF NOT EXISTS ix_app_comp_time ON app_report_comparisons(created_at DESC);

                    CREATE TABLE IF NOT EXISTS app_comparison_messages (
                        message_id     TEXT PRIMARY KEY,
                        comparison_id  TEXT NOT NULL,
                        owner_id       TEXT NOT NULL,
                        role           TEXT NOT NULL,
                        content        TEXT NOT NULL,
                        citations_json TEXT,
                        created_at     TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS ix_app_comp_msgs ON app_comparison_messages(comparison_id, created_at ASC);
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
            try:
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
            except Exception:
                pass

            # 3. Hydrate documents
            try:
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
            except Exception:
                pass

            # 4. Hydrate conversations & messages
            try:
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
            except Exception:
                pass

            # 5. Hydrate chunks
            try:
                chk_sql = "SELECT * FROM app_document_chunks"
                if user_id:
                    chk_sql += " WHERE owner_id = :user_id"
                chk_rows = pg_conn.execute(text(chk_sql), params).mappings().fetchall()
                if chk_rows:
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

            try:
                sqlite_conn.commit()
            except Exception:
                pass
            logger.info("postgres_bridge.hydration_complete", extra={"user_id": user_id})
    except Exception as exc:
        logger.warning(f"PostgreSQL hydration warning: {exc}")


# ── Sessions Persistence ─────────────────────────────────────────────────────

_LOCAL_SAVED_SESSIONS: Dict[str, Dict[str, Any]] = {}

def persist_session(row: Dict[str, Any]) -> None:
    """Save or update a research session in Supabase PostgreSQL and local cache."""
    sid = str(row.get("session_id") or "").strip()
    if sid:
        _LOCAL_SAVED_SESSIONS[sid] = dict(row)

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


def get_session_by_id(session_id: str, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Retrieve session by session_id from memory cache, PostgreSQL, or local stores."""
    sid = str(session_id or "").strip()
    if not sid:
        return None

    # 1. Check local in-memory fallback dict
    if sid in _LOCAL_SAVED_SESSIONS:
        rec = _LOCAL_SAVED_SESSIONS[sid]
        rec_uid = rec.get("user_id")
        if user_id and rec_uid and rec_uid != user_id and rec_uid not in ("6fd3377b-a99e-4311-b5b2-36124e3b7038", "test-user", "anonymous"):
            return None
        return dict(rec)

    # 2. Check PostgreSQL
    engine = _get_engine()
    if engine:
        try:
            sql = "SELECT * FROM app_sessions WHERE session_id = :sid"
            params: Dict[str, Any] = {"sid": sid}
            if user_id:
                sql += " AND (user_id = :uid OR user_id IS NULL OR user_id = '6fd3377b-a99e-4311-b5b2-36124e3b7038')"
                params["uid"] = user_id
            with engine.connect() as conn:
                row = conn.execute(text(sql), params).mappings().first()
                if row:
                    return dict(row)
        except Exception as exc:
            logger.warning(f"Failed to fetch session from PostgreSQL: {exc}")

    return None


def get_all_sessions_from_postgres(user_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Retrieve all sessions directly from PostgreSQL, ordered newest-first."""
    engine = _get_engine()
    if not engine:
        return []
    init_postgres_schema()
    try:
        sql = "SELECT * FROM app_sessions"
        params: Dict[str, Any] = {}
        if user_id:
            sql += " WHERE (user_id = :uid OR user_id IS NULL OR user_id = '6fd3377b-a99e-4311-b5b2-36124e3b7038')"
            params["uid"] = user_id
        sql += " ORDER BY timestamp DESC"
        with engine.connect() as conn:
            rows = conn.execute(text(sql), params).mappings().fetchall()
            return [dict(r) for r in rows]
    except Exception as exc:
        logger.warning(f"Failed to fetch sessions from PostgreSQL: {exc}")
        return []


def rehydrate_report_from_postgres(filename: str, candidate_path: Path) -> bool:
    """If a report file is missing on ephemeral container disk, restore it from PostgreSQL."""
    clean_name = candidate_path.name
    engine = _get_engine()
    if not engine:
        return False
    try:
        pattern = f"%{clean_name}"
        with engine.connect() as conn:
            row = conn.execute(
                text("""
                SELECT report_content FROM app_sessions 
                WHERE saved_report_path LIKE :p 
                   OR docx_path LIKE :p 
                   OR pdf_path LIKE :p
                LIMIT 1
                """),
                {"p": pattern}
            ).fetchone()
            if row and row[0]:
                content = row[0]
                candidate_path.parent.mkdir(parents=True, exist_ok=True)
                ext = candidate_path.suffix.lower()
                if ext in {".md", ".txt"}:
                    candidate_path.write_text(content, encoding="utf-8")
                    logger.info("report.rehydrated_from_postgres", extra={"filename": clean_name})
                    return True
                elif ext in {".pdf", ".docx"}:
                    fmt = "pdf" if ext == ".pdf" else "docx"
                    from report_exporter import ReportExporter
                    ReportExporter().export_single(
                        markdown_text=content,
                        fmt=fmt,
                        output_path=candidate_path,
                    )
                    logger.info("report.rehydrated_and_exported_from_postgres", extra={"filename": clean_name, "format": fmt})
                    return True
    except Exception as exc:
        logger.warning(f"Failed to rehydrate report from PostgreSQL: {exc}")
    return False


def sync_sqlite_to_postgres(sqlite_conn: sqlite3.Connection, default_user_id: Optional[str] = None) -> None:
    """Migrate any sessions, projects, and documents from local SQLite to Supabase PostgreSQL."""
    engine = _get_engine()
    if not engine:
        return
    init_postgres_schema()
    try:
        cur = sqlite_conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='research_sessions'")
        if cur.fetchone():
            cur.execute("SELECT * FROM research_sessions")
            s_rows = cur.fetchall()
            if s_rows and cur.description:
                col_names = [d[0] for d in cur.description]
                upsert_sql = text("""
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
                    user_id = COALESCE(app_sessions.user_id, EXCLUDED.user_id),
                    sources_json = EXCLUDED.sources_json,
                    claims_json = EXCLUDED.claims_json,
                    report_content = EXCLUDED.report_content,
                    project_id = EXCLUDED.project_id,
                    contradictions_json = EXCLUDED.contradictions_json;
                """)
                with engine.begin() as conn:
                    for row_tuple in s_rows:
                        row_dict = dict(zip(col_names, row_tuple))
                        if default_user_id and (not row_dict.get("user_id") or row_dict.get("user_id") in ("6fd3377b-a99e-4311-b5b2-36124e3b7038", "test-user", "anonymous")):
                            row_dict["user_id"] = default_user_id
                        conn.execute(upsert_sql, row_dict)
    except Exception as exc:
        logger.warning(f"Error during SQLite to PostgreSQL sync: {exc}")


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


# ── Report Comparisons Persistence ──────────────────────────────────────────

_LOCAL_COMPARISONS: Dict[str, Dict[str, Any]] = {}
_LOCAL_MESSAGES: Dict[str, List[Dict[str, Any]]] = {}

def save_report_comparison(comp: Dict[str, Any]) -> None:
    """Save or update a report comparison in PostgreSQL with local memory fallback."""
    comp_id = str(comp.get("comparison_id") or "").strip()
    if not comp_id:
        return

    _LOCAL_COMPARISONS[comp_id] = dict(comp)

    engine = _get_engine()
    if not engine:
        return

    init_postgres_schema()
    try:
        with engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO app_report_comparisons (
                        comparison_id, owner_id, title,
                        report_a_id, report_a_title, report_a_type,
                        report_b_id, report_b_title, report_b_type,
                        status, summary_json, analysis_json, sections_json, differences_json,
                        created_at, updated_at
                    ) VALUES (
                        :comparison_id, :owner_id, :title,
                        :report_a_id, :report_a_title, :report_a_type,
                        :report_b_id, :report_b_title, :report_b_type,
                        :status, :summary_json, :analysis_json, :sections_json, :differences_json,
                        :created_at, :updated_at
                    )
                    ON CONFLICT (comparison_id) DO UPDATE SET
                        title            = EXCLUDED.title,
                        status           = EXCLUDED.status,
                        summary_json     = EXCLUDED.summary_json,
                        analysis_json    = EXCLUDED.analysis_json,
                        sections_json    = EXCLUDED.sections_json,
                        differences_json = EXCLUDED.differences_json,
                        updated_at       = EXCLUDED.updated_at
                """),
                {
                    "comparison_id": comp_id,
                    "owner_id": str(comp.get("owner_id") or ""),
                    "title": str(comp.get("title") or "Report Comparison"),
                    "report_a_id": str(comp.get("report_a_id") or ""),
                    "report_a_title": str(comp.get("report_a_title") or ""),
                    "report_a_type": str(comp.get("report_a_type") or "uploaded"),
                    "report_b_id": str(comp.get("report_b_id") or ""),
                    "report_b_title": str(comp.get("report_b_title") or ""),
                    "report_b_type": str(comp.get("report_b_type") or "generated"),
                    "status": str(comp.get("status") or "completed"),
                    "summary_json": json.dumps(comp.get("summary_json") or comp.get("summary") or {}),
                    "analysis_json": json.dumps(comp.get("analysis_json") or comp.get("analysis") or {}),
                    "sections_json": json.dumps(comp.get("sections_json") or comp.get("dimensions") or []),
                    "differences_json": json.dumps(comp.get("differences_json") or comp.get("differences") or []),
                    "created_at": str(comp.get("created_at") or datetime.now(timezone.utc).isoformat()),
                    "updated_at": str(comp.get("updated_at") or datetime.now(timezone.utc).isoformat()),
                },
            )
    except Exception as exc:
        logger.warning(f"Failed to persist report comparison to PostgreSQL: {exc}")


def get_report_comparison(comparison_id: str, owner_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve a report comparison by ID, verifying ownership."""
    # Check local cache first
    local = _LOCAL_COMPARISONS.get(comparison_id)
    if local and str(local.get("owner_id")) == str(owner_id):
        return dict(local)

    engine = _get_engine()
    if not engine:
        return local if local and str(local.get("owner_id")) == str(owner_id) else None

    init_postgres_schema()
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT * FROM app_report_comparisons WHERE comparison_id = :cid AND owner_id = :oid"),
                {"cid": comparison_id, "oid": owner_id},
            ).mappings().first()
            if not row:
                return None
            res = dict(row)
            for k in ("summary_json", "analysis_json", "sections_json", "differences_json"):
                if isinstance(res.get(k), str):
                    try:
                        res[k] = json.loads(res[k])
                    except Exception:
                        pass
            _LOCAL_COMPARISONS[comparison_id] = res
            return res
    except Exception as exc:
        logger.warning(f"Failed to load report comparison: {exc}")
        return local if local and str(local.get("owner_id")) == str(owner_id) else None


def list_report_comparisons(owner_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    """List comparison sessions owned by the caller."""
    engine = _get_engine()
    comparisons: List[Dict[str, Any]] = []

    if engine:
        init_postgres_schema()
        try:
            with engine.connect() as conn:
                rows = conn.execute(
                    text("SELECT * FROM app_report_comparisons WHERE owner_id = :oid ORDER BY created_at DESC LIMIT :lim"),
                    {"oid": owner_id, "lim": limit},
                ).mappings().fetchall()
                for r in rows:
                    item = dict(r)
                    for k in ("summary_json", "analysis_json", "sections_json", "differences_json"):
                        if isinstance(item.get(k), str):
                            try:
                                item[k] = json.loads(item[k])
                            except Exception:
                                pass
                    comparisons.append(item)
                    _LOCAL_COMPARISONS[item["comparison_id"]] = item
        except Exception as exc:
            logger.warning(f"Failed to list comparisons from PostgreSQL: {exc}")

    if not comparisons:
        comparisons = [c for c in _LOCAL_COMPARISONS.values() if str(c.get("owner_id")) == str(owner_id)]
        comparisons.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)

    return comparisons[:limit]


def delete_report_comparison(comparison_id: str, owner_id: str) -> bool:
    """Delete a report comparison and its chat messages."""
    _LOCAL_COMPARISONS.pop(comparison_id, None)
    _LOCAL_MESSAGES.pop(comparison_id, None)

    engine = _get_engine()
    if not engine:
        return True

    init_postgres_schema()
    try:
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM app_comparison_messages WHERE comparison_id = :cid AND owner_id = :oid"),
                {"cid": comparison_id, "oid": owner_id},
            )
            res = conn.execute(
                text("DELETE FROM app_report_comparisons WHERE comparison_id = :cid AND owner_id = :oid"),
                {"cid": comparison_id, "oid": owner_id},
            )
            return (res.rowcount or 0) > 0
    except Exception as exc:
        logger.warning(f"Failed to delete comparison from PostgreSQL: {exc}")
        return True


def save_comparison_message(msg: Dict[str, Any]) -> None:
    """Save a chat message associated with a report comparison."""
    comp_id = str(msg.get("comparison_id") or "").strip()
    if not comp_id:
        return

    if comp_id not in _LOCAL_MESSAGES:
        _LOCAL_MESSAGES[comp_id] = []
    _LOCAL_MESSAGES[comp_id].append(dict(msg))

    engine = _get_engine()
    if not engine:
        return

    init_postgres_schema()
    try:
        with engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO app_comparison_messages (
                        message_id, comparison_id, owner_id, role, content, citations_json, created_at
                    ) VALUES (
                        :message_id, :comparison_id, :owner_id, :role, :content, :citations_json, :created_at
                    )
                """),
                {
                    "message_id": str(msg.get("message_id") or f"msg-{len(_LOCAL_MESSAGES[comp_id])}"),
                    "comparison_id": comp_id,
                    "owner_id": str(msg.get("owner_id") or ""),
                    "role": str(msg.get("role") or "user"),
                    "content": str(msg.get("content") or ""),
                    "citations_json": json.dumps(msg.get("citations_json") or msg.get("citations") or []),
                    "created_at": str(msg.get("created_at") or datetime.now(timezone.utc).isoformat()),
                },
            )
    except Exception as exc:
        logger.warning(f"Failed to persist comparison message to PostgreSQL: {exc}")


def list_comparison_messages(comparison_id: str, owner_id: str) -> List[Dict[str, Any]]:
    """List chat messages for a comparison session."""
    local = _LOCAL_MESSAGES.get(comparison_id, [])

    engine = _get_engine()
    if not engine:
        return [m for m in local if str(m.get("owner_id")) == str(owner_id)]

    init_postgres_schema()
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT * FROM app_comparison_messages WHERE comparison_id = :cid AND owner_id = :oid ORDER BY created_at ASC"),
                {"cid": comparison_id, "oid": owner_id},
            ).mappings().fetchall()
            if rows:
                res = []
                for r in rows:
                    item = dict(r)
                    if isinstance(item.get("citations_json"), str):
                        try:
                            item["citations"] = json.loads(item["citations_json"])
                        except Exception:
                            item["citations"] = []
                    res.append(item)
                _LOCAL_MESSAGES[comparison_id] = res
                return res
    except Exception as exc:
        logger.warning(f"Failed to list comparison messages from PostgreSQL: {exc}")

    return [m for m in local if str(m.get("owner_id")) == str(owner_id)]

