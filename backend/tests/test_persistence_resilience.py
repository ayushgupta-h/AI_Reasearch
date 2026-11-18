"""
backend/tests/test_persistence_resilience.py
============================================
Automated verification for permanent PostgreSQL data persistence across
Render container restarts, redeployments, and session re-logins.
"""

import os
import uuid
import tempfile
from pathlib import Path
import pytest
from sqlalchemy import text

from app.config import get_settings
from app.database.session import check_connection, get_engine
from app.infrastructure.postgres_bridge import (
    init_postgres_schema,
    persist_session,
    get_session_by_id,
    get_all_sessions_from_postgres,
    rehydrate_report_from_postgres,
    sync_sqlite_to_postgres,
    delete_session,
)
from session_manager import SessionManager


@pytest.fixture(scope="module")
def pg_engine():
    settings = get_settings()
    if not settings.database_url or not check_connection(settings):
        pytest.skip("Supabase PostgreSQL database is not reachable from this environment.")
    init_postgres_schema()
    return get_engine(settings)


def test_postgresql_connection_and_schema(pg_engine):
    """Verify that PostgreSQL is reachable and core persistence tables exist."""
    with pg_engine.connect() as conn:
        tables = [
            r[0]
            for r in conn.execute(
                text("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
            ).fetchall()
        ]
        assert "app_sessions" in tables
        assert "app_projects" in tables
        assert "app_documents" in tables


def test_session_persistence_postgresql(pg_engine):
    """Verify that saving a research session writes directly to PostgreSQL."""
    test_user_a = f"test-user-a-{uuid.uuid4().hex[:8]}"
    test_session_id = f"test-session-{uuid.uuid4().hex[:12]}"

    row = {
        "session_id": test_session_id,
        "topic": "Quantum Computing Scalability Analysis",
        "timestamp": "2026-11-18T12:00:00",
        "provider": "gemini",
        "critic_score": 9,
        "revision_count": 1,
        "status": "completed",
        "saved_report_path": f"backend/reports/{test_session_id}.md",
        "docx_path": "",
        "pdf_path": "",
        "citations_verified": 8,
        "citations_total": 8,
        "duration_seconds": 45.2,
        "notes": "automated-resilience-test",
        "user_id": test_user_a,
        "sources_json": "[]",
        "claims_json": "[]",
        "report_content": "# Quantum Computing Report\nSynthesized findings.",
        "project_id": None,
        "contradictions_json": "[]",
    }

    try:
        # Persist session
        persist_session(row)

        # Retrieve by ID
        fetched = get_session_by_id(test_session_id, user_id=test_user_a)
        assert fetched is not None
        assert fetched["topic"] == "Quantum Computing Scalability Analysis"
        assert fetched["user_id"] == test_user_a
        assert "Quantum Computing Report" in fetched["report_content"]

        # Retrieve in all sessions list for user
        user_sessions = get_all_sessions_from_postgres(user_id=test_user_a)
        session_ids = [s["session_id"] for s in user_sessions]
        assert test_session_id in session_ids

    finally:
        delete_session(test_session_id, user_id=test_user_a)


def test_user_isolation(pg_engine):
    """Verify that User B cannot retrieve User A's private research session."""
    user_a = f"user-a-{uuid.uuid4().hex[:8]}"
    user_b = f"user-b-{uuid.uuid4().hex[:8]}"
    sid = f"isolated-session-{uuid.uuid4().hex[:12]}"

    row = {
        "session_id": sid,
        "topic": "Confidential Research Subject",
        "timestamp": "2026-11-18T12:05:00",
        "provider": "gemini",
        "critic_score": 10,
        "revision_count": 0,
        "status": "completed",
        "saved_report_path": "",
        "docx_path": "",
        "pdf_path": "",
        "citations_verified": 3,
        "citations_total": 3,
        "duration_seconds": 15.0,
        "notes": "",
        "user_id": user_a,
        "sources_json": "[]",
        "claims_json": "[]",
        "report_content": "Secret content",
        "project_id": None,
        "contradictions_json": "[]",
    }

    try:
        persist_session(row)

        # User A can retrieve it
        assert get_session_by_id(sid, user_id=user_a) is not None

        # User B CANNOT retrieve it
        assert get_session_by_id(sid, user_id=user_b) is None

        # User B's list does not contain it
        user_b_list = get_all_sessions_from_postgres(user_id=user_b)
        assert sid not in [s["session_id"] for s in user_b_list]

    finally:
        delete_session(sid, user_id=user_a)


def test_container_restart_empty_sqlite_retrieval(pg_engine):
    """Simulate a fresh Render container restart with empty SQLite filesystem."""
    user_id = f"restarted-user-{uuid.uuid4().hex[:8]}"
    sid = f"restarted-session-{uuid.uuid4().hex[:12]}"

    row = {
        "session_id": sid,
        "topic": "Post-Container-Restart Durability Test",
        "timestamp": "2026-11-18T12:10:00",
        "provider": "gemini",
        "critic_score": 8,
        "revision_count": 1,
        "status": "completed",
        "saved_report_path": "",
        "docx_path": "",
        "pdf_path": "",
        "citations_verified": 4,
        "citations_total": 4,
        "duration_seconds": 22.0,
        "notes": "",
        "user_id": user_id,
        "sources_json": "[]",
        "claims_json": "[]",
        "report_content": "Durable content across container restarts",
        "project_id": None,
        "contradictions_json": "[]",
    }

    try:
        persist_session(row)

        # Create SessionManager with a brand new, empty SQLite database in a temp directory
        with tempfile.TemporaryDirectory() as tmpdir:
            fresh_db_path = Path(tmpdir) / "empty_sessions.db"
            assert not fresh_db_path.exists()

            sm = SessionManager(db_path=str(fresh_db_path))

            # Query all sessions for user
            recovered = sm.get_all_sessions(user_id=user_id)
            recovered_ids = [s["session_id"] for s in recovered]
            assert sid in recovered_ids, "Session lost after container restart simulation!"

            # Query single session
            single = sm.get_session(sid, user_id=user_id)
            assert single is not None
            assert single["topic"] == "Post-Container-Restart Durability Test"
            assert "Durable content" in single["content"]

    finally:
        delete_session(sid, user_id=user_id)


def test_report_file_rehydration_from_postgres(pg_engine):
    """Verify that when a physical report file is absent on disk, it is reconstructed from DB."""
    sid = f"rehydrate-session-{uuid.uuid4().hex[:12]}"
    filename = f"report_test_rehydration_{sid}.md"
    expected_content = "# Auto Rehydrated Report Title\n\nThis content was recovered directly from PostgreSQL."

    row = {
        "session_id": sid,
        "topic": "On-demand Rehydration Test",
        "timestamp": "2026-11-18T12:15:00",
        "provider": "gemini",
        "critic_score": 9,
        "revision_count": 0,
        "status": "completed",
        "saved_report_path": f"backend/reports/{filename}",
        "docx_path": "",
        "pdf_path": "",
        "citations_verified": 2,
        "citations_total": 2,
        "duration_seconds": 10.0,
        "notes": "",
        "user_id": "test-user-rehydration",
        "sources_json": "[]",
        "claims_json": "[]",
        "report_content": expected_content,
        "project_id": None,
        "contradictions_json": "[]",
    }

    try:
        persist_session(row)

        with tempfile.TemporaryDirectory() as tmpdir:
            target_path = Path(tmpdir) / filename
            assert not target_path.exists()

            success = rehydrate_report_from_postgres(filename, target_path)
            assert success is True
            assert target_path.is_file()

            read_back = target_path.read_text(encoding="utf-8")
            assert read_back == expected_content

    finally:
        delete_session(sid, user_id="test-user-rehydration")
