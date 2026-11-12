"""
tests/test_migration_planner.py
===============================
Tests for the SQLite → PostgreSQL migration planning. These never touch
PostgreSQL; they verify the read-only inspection, ownership handling, ID
preservation and source-file integrity.
"""

import sqlite3
import uuid

from pathlib import Path

from backend.db_tools.migrate_sessions_to_postgres import (
    _parse_uuid,
    plan_migration,
    uuid_value_from_owner,
)

OWNED_SUB = str(uuid.uuid4())


def _build_source(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE research_sessions (
            session_id TEXT PRIMARY KEY,
            topic TEXT,
            timestamp TEXT,
            provider TEXT,
            critic_score INTEGER,
            revision_count INTEGER,
            status TEXT,
            user_id TEXT
        )
        """
    )
    conn.executemany(
        """
        INSERT INTO research_sessions
            (session_id, topic, timestamp, provider, critic_score,
             revision_count, status, user_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("11111111-1111-1111-1111-111111111111", "Physics", "2026-01-01T00:00:00Z",
             "groq", 9, 1, "completed", OWNED_SUB),
            ("22222222-2222-2222-2222-222222222222", "History", "2026-01-02T00:00:00Z",
             "gemini", 8, 0, "completed", OWNED_SUB),
            # unowned → must be skipped without a default owner
            ("33333333-3333-3333-3333-333333333333", "Orphan", "2026-01-03T00:00:00Z",
             "groq", 7, 0, "completed", None),
            # invalid legacy id → must get a fresh UUID
            ("not-a-uuid", "Legacy", "2026-01-04T00:00:00Z",
             "mistral", 6, 0, "completed", OWNED_SUB),
        ],
    )
    conn.commit()
    conn.close()


def test_plan_migration_counts(tmp_path):
    source = tmp_path / "sessions.db"
    _build_source(source)

    report = plan_migration(source)

    assert report.source_total == 4
    assert report.migrated == 3
    assert report.skipped_unowned == 1
    assert len(report.missing_owner_rows) == 1
    assert "not-a-uuid" in report.assigned_new_ids


def test_plan_migration_with_default_owner(tmp_path):
    source = tmp_path / "sessions.db"
    _build_source(source)

    report = plan_migration(source, default_owner=OWNED_SUB)

    assert report.migrated == 4
    assert report.skipped_unowned == 0


def test_source_file_never_modified(tmp_path):
    source = tmp_path / "sessions.db"
    _build_source(source)
    before = source.read_bytes()

    plan_migration(source)
    plan_migration(source, default_owner=OWNED_SUB)

    assert source.read_bytes() == before


def test_sessions_db_exists_untouched():
    # The real production file must still exist and be readable.
    real = Path(__file__).resolve().parent.parent / "sessions.db"
    assert real.exists(), "sessions.db is missing — do not delete it"


def test_uuid_value_from_owner_deterministic():
    a = uuid_value_from_owner(OWNED_SUB)
    b = uuid_value_from_owner(OWNED_SUB)
    assert a == b
    assert isinstance(a, uuid.UUID)


def test_parse_uuid_rejects_missing():
    assert _parse_uuid(None) is None
    assert _parse_uuid("") is None
    assert _parse_uuid("not-a-uuid") is None
