"""
tools/migrate_sessions_to_postgres.py
=====================================
One-shot, verified migration of the legacy ``research_sessions`` records from
SQLite into the new PostgreSQL ``research_sessions`` table.

Guarantees
----------
* The SQLite database is opened **read-only** (``mode=ro``) and is never
  modified or deleted.
* Session UUIDs are preserved: the ``session_id`` TEXT value becomes the
  ``id`` UUID column (when it parses as a UUID), else a fresh UUID is assigned
  and recorded in the report.
* Ownership is mapped from ``user_id``. Records with a NULL ``user_id`` are
  **not** migrated unless ``--default-owner`` (a Supabase Auth ``sub``) is
  provided; they are listed in the report instead.
* Credentials are never printed. The destination is reached through the
  ``DATABASE_URL`` environment variable.

Usage
-----
    cd backend
    set DATABASE_URL=postgresql://...  # (or put it in backend/.env)
    python -m tools.migrate_sessions_to_postgres --dry-run
    python -m tools.migrate_sessions_to_postgres --default-owner <sub>
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

# Allow running as a module from the backend/ directory.
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import load_env, get_settings  # noqa: E402
from app.database.session import get_session_factory  # noqa: E402
from app.database.models.domain import ResearchSession  # noqa: E402

load_env()


@dataclass
class MigrationReport:
    """Summary of what the migration did / would do."""

    source_total: int = 0
    migrated: int = 0
    skipped_unowned: int = 0
    missing_owner_rows: List[Mapping[str, Any]] = field(default_factory=list)
    assigned_new_ids: Dict[str, str] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)


def _parse_timestamp(raw: Optional[str]) -> Optional[datetime]:
    """Parse the legacy ISO text timestamp, or return None."""
    if not raw:
        return None
    try:
        moment = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc)
    except ValueError:
        return None


def _parse_uuid(raw: Optional[str]) -> Optional[uuid.UUID]:
    try:
        return uuid.UUID(str(raw))
    except (ValueError, TypeError):
        return None


def read_source_sessions(sqlite_path: Path) -> List[Mapping[str, Any]]:
    """Open the legacy DB read-only and return all session rows."""
    uri = f"file:{sqlite_path.as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT * FROM research_sessions ORDER BY timestamp"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def plan_migration(
    sqlite_path: Path, default_owner: Optional[str] = None
) -> MigrationReport:
    """Inspect the source and prepare a safe migration plan (no DB writes)."""
    report = MigrationReport()
    rows = read_source_sessions(sqlite_path)
    report.source_total = len(rows)
    for row in rows:
        owner_raw = row.get("user_id")
        if not owner_raw and not default_owner:
            report.skipped_unowned += 1
            report.missing_owner_rows.append(row)
            continue
        session_id = row.get("session_id")
        parsed = _parse_uuid(session_id)
        if parsed is None:
            report.assigned_new_ids[str(session_id)] = str(uuid.uuid4())
        report.migrated += 1
    return report


def run_migration(
    sqlite_path: Path,
    *,
    default_owner: Optional[str] = None,
    dry_run: bool = True,
) -> MigrationReport:
    """Execute (or preview) the migration with verification."""
    settings = get_settings()
    report = plan_migration(sqlite_path, default_owner=default_owner)

    if not settings.database_url:
        report.errors.append("DATABASE_URL is not configured; cannot migrate.")
        return report
    if not sqlite_path.exists():
        report.errors.append(f"SQLite file not found: {sqlite_path}")
        return report

    if dry_run:
        return report

    # Live path: only reached when DATABASE_URL is set and connectivity holds.
    factory = get_session_factory(settings)
    session = factory()
    inserted = 0
    try:
        with session.begin():
            for row in read_source_sessions(sqlite_path):
                owner_raw = row.get("user_id")
                if not owner_raw and not default_owner:
                    continue
                owner = owner_raw or default_owner or ""
                uuid_value = _parse_uuid(row.get("session_id")) or uuid.uuid4()
                if session.get(ResearchSession, uuid_value) is not None:
                    continue  # idempotent: preserve existing record
                session.add(
                    ResearchSession(
                        id=uuid_value,
                        project_id=None,
                        owner_id=uuid_value_from_owner(owner),
                        topic=_text(row.get("topic"), "Untitled research"),
                        provider=_text(row.get("provider"), None),
                        status=_text(row.get("status"), "completed"),
                        priority="normal",
                        notes=_text(row.get("notes"), None),
                        created_at=_parse_timestamp(row.get("timestamp"))
                        or datetime.now(timezone.utc),
                        updated_at=_parse_timestamp(row.get("timestamp"))
                        or datetime.now(timezone.utc),
                    )
                )
                inserted += 1
        # Verification: destination count must match the planned migrated count.
        from sqlalchemy import func, select

        got = int(session.scalar(select(func.count()).select_from(ResearchSession)) or 0)
        expected = report.migrated
        if got < expected or inserted != expected - report.skipped_unowned:
            report.errors.append(
                f"Verification failed: destination={got}, expected={expected}, "
                f"inserted={inserted}, skipped_unowned={report.skipped_unowned}."
            )
        elif inserted == 0:
            report.errors.append("No records were inserted; nothing to migrate.")
        else:
            report.migrated = inserted
        session.commit()
    finally:
        session.close()
    return report


def uuid_value_from_owner(owner: str) -> uuid.UUID:
    """Map an owner string (Supabase sub) to a UUID value."""
    parsed = _parse_uuid(owner)
    if parsed is not None:
        return parsed
    # Deterministic UUID from a non-UUID sub (v5 namespace).
    return uuid.uuid5(uuid.NAMESPACE_URL, owner)


def _text(value: Any, default: Optional[str]) -> Optional[str]:
    if value is None:
        return default
    return str(value)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sqlite-path",
        default=str(BACKEND_DIR / "sessions.db"),
        help="Path to the legacy sessions.db (default: backend/sessions.db).",
    )
    parser.add_argument(
        "--default-owner",
        default=None,
        help="Supabase Auth sub to own unowned records (otherwise they are skipped).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Inspect and plan only; do not write to PostgreSQL.",
    )
    args = parser.parse_args(argv)

    sqlite_path = Path(args.sqlite_path)
    if not sqlite_path.exists():
        print(f"ERROR: SQLite file not found: {sqlite_path}", file=sys.stderr)
        return 2

    if args.dry_run:
        report = plan_migration(sqlite_path, default_owner=args.default_owner)
    else:
        report = run_migration(
            sqlite_path, default_owner=args.default_owner, dry_run=False
        )

    print(f"Source records:            {report.source_total}")
    print(f"Planned/executed:        {report.migrated}")
    print(f"Skipped (unowned):       {report.skipped_unowned}")
    if report.assigned_new_ids:
        print("Assigned new UUIDs (invalid legacy session_id):")
        for old, new in report.assigned_new_ids.items():
            print(f"  {old} -> {new}")
    if report.errors:
        print("Errors:")
        for error in report.errors:
            print(f"  ERROR: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
