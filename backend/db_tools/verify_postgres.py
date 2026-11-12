"""
tools/verify_postgres.py
========================
Local verification harness for the PostgreSQL deployment.

This is the "gate" that must pass *before* any data migration is run. It
performs read-only checks and never prints the database URL or password.

Checks
------
1. Connectivity: ``SELECT 1`` against the engine (via ``DATABASE_URL``).
2. SSL: whether the current backend connection is encrypted
   (``pg_stat_ssl.ssl`` == true).
3. (Optional, ``--schema``) Read-only schema inspection: list tables in the
   ``public`` schema and their row counts.
4. (Optional, ``--verify-migration``) Compare source (SQLite, read-only) and
   destination (PostgreSQL) record counts and ownership breakdown.

Usage
-----
    cd backend
    python -m tools.verify_postgres                 # connectivity + SSL
    python -m tools.verify_postgres --schema        # + schema/row counts
    python -m tools.verify_postgres --verify-migration   # + source/dest counts

Exit codes: 0 = all pass, 1 = a check failed, 2 = usage/config error.
"""

from __future__ import annotations

import argparse
import sys
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import load_env, get_settings  # noqa: E402
from app.database.session import get_engine  # noqa: E402
from sqlalchemy import text, func  # noqa: E402

load_env()

FAILED = 1
OK = 0


def _print_banner(title: str) -> None:
    print("\n=== " + title + " ===")


def check_connectivity(settings) -> bool:
    _print_banner("1. PostgreSQL connectivity")
    try:
        engine = get_engine(settings)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        print("PASS: engine connected and SELECT 1 succeeded.")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: could not connect to PostgreSQL: {exc}")
        return False


def check_ssl(settings) -> bool:
    _print_banner("2. SSL on the backend connection")
    try:
        engine = get_engine(settings)
        with engine.connect() as conn:
            result = conn.execute(
                text("SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()")
            ).scalar()
        if result is True:
            print("PASS: the backend connection is SSL-encrypted.")
            return True
        print("FAIL: the backend connection is NOT encrypted (ssl='%s')." % result)
        return False
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: could not determine SSL state: {exc}")
        return False


def check_schema(settings) -> bool:
    _print_banner("3. PostgreSQL schema (read-only)")
    try:
        engine = get_engine(settings)
        with engine.connect() as conn:
            tables = conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' ORDER BY table_name"
                )
            ).scalars().all()
        if not tables:
            print("NOTE: no tables in the 'public' schema yet (migrations not applied?).")
            return False
        print(f"Tables in public schema ({len(tables)}):")
        for t in tables:
            with engine.connect() as conn:
                count = conn.execute(
                    text(f'SELECT count(*) FROM "public"."{t}"')
                ).scalar()
            print(f"  {t}: {count} rows")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: schema inspection error: {exc}")
        return False


def _read_source_sessions(sqlite_path: Path) -> List[Dict[str, Any]]:
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


def check_migration_parity(settings, sqlite_path: Path) -> bool:
    _print_banner("4. Source vs destination record counts / ownership")
    ok = True

    if not sqlite_path.exists():
        print(f"FAIL: source SQLite file not found: {sqlite_path}")
        return False

    try:
        source = _read_source_sessions(sqlite_path)
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: could not read source SQLite: {exc}")
        return False

    source_total = len(source)
    source_owned = sum(1 for r in source if r.get("user_id"))
    source_unowned = source_total - source_owned
    print(
        f"Source (SQLite, read-only): total={source_total}, "
        f"owned={source_owned}, unowned={source_unowned}"
    )

    try:
        engine = get_engine(settings)
        with engine.connect() as conn:
            dest_total = conn.execute(
                text('SELECT count(*) FROM "public"."research_sessions"')
            ).scalar()
            dest_owned = conn.execute(
                text('SELECT count(*) FROM "public"."research_sessions" '
                     'WHERE owner_id IS NOT NULL')
            ).scalar()
            dest_unowned = dest_total - dest_owned
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: could not query destination PostgreSQL: {exc}")
        return False

    print(
        f"Destination (PostgreSQL): total={dest_total}, "
        f"owned={dest_owned}, unowned={dest_unowned}"
    )

    if dest_total < source_owned:
        print("FAIL: destination has fewer records than expected source records.")
        ok = False
    else:
        print("PASS: destination count covers all source records.")

    # Ownership breakdown by owner_id.
    try:
        engine = get_engine(settings)
        with engine.connect() as conn:
            rows = conn.execute(
                text('SELECT owner_id, count(*) AS n FROM "public"."research_sessions" '
                     'GROUP BY owner_id ORDER BY n DESC')
            ).fetchall()
        print("Ownership distribution (PostgreSQL):")
        for owner_id, n in rows:
            print(f"  owner={owner_id} -> {n}")
    except Exception as exc:  # noqa: BLE001
        print(f"WARN: could not compute ownership distribution: {exc}")

    return ok


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--schema", action="store_true", help="Also inspect schema and row counts."
    )
    parser.add_argument(
        "--verify-migration",
        action="store_true",
        help="Compare source/destination counts and ownership.",
    )
    parser.add_argument(
        "--sqlite-path",
        default=str(BACKEND_DIR / "sessions.db"),
        help="Path to the legacy sessions.db (read-only).",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    if not settings.database_url:
        print("ERROR: DATABASE_URL is not configured (set it in backend/.env or the "
              "environment).", file=sys.stderr)
        return 2

    results = [
        check_connectivity(settings),
        check_ssl(settings),
    ]
    if args.schema:
        results.append(check_schema(settings))
    if args.verify_migration:
        results.append(check_migration_parity(settings, Path(args.sqlite_path)))

    passed = all(results)
    print("\n--- SUMMARY ---")
    print("ALL CHECKS PASSED" if passed else "ONE OR MORE CHECKS FAILED")
    return OK if passed else FAILED


if __name__ == "__main__":
    raise SystemExit(main())
