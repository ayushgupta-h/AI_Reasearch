"""
Session Manager
================
Persists Research AI pipeline sessions to a local SQLite database.

Each session record stores:
  - session_id   : UUID primary key
  - topic        : Research topic string
  - timestamp    : ISO-8601 datetime of when the run completed
  - provider     : LLM provider used (gemini, openai, etc.)
  - critic_score : Final critic quality score (0-10)
  - revision_count : Number of self-correction cycles performed
  - status       : Pipeline terminal status ("completed", "failed", etc.)
  - saved_report_path  : Absolute path to the saved .md report
  - docx_path    : Path to .docx export (may be empty)
  - pdf_path     : Path to .pdf export (may be empty)
  - citations_verified : Number of live citations validated
  - citations_total    : Total citations checked
  - duration_seconds   : Wall-clock time for the full pipeline run

Usage::

    from session_manager import SessionManager

    sm = SessionManager()

    # Save a completed pipeline result
    session_id = sm.save_session(final_state, duration_seconds=142.3)

    # List all past sessions
    sm.display_history()

    # Retrieve a specific session
    record = sm.get_session(session_id)

    # Delete old sessions
    sm.delete_session(session_id)
"""

from __future__ import annotations

import sys
import sqlite3
import uuid
import datetime
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

if sys.platform.startswith("win"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

console = Console()

# Default database location — stored alongside the project
_DEFAULT_DB_PATH = str(Path(__file__).parent / "sessions.db")

# ─── Schema ───────────────────────────────────────────────────────────────────

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS research_sessions (
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
    user_id             TEXT
);
"""

_INSERT_SESSION_SQL = """
INSERT INTO research_sessions (
    session_id, topic, timestamp, provider, critic_score,
    revision_count, status, saved_report_path, docx_path, pdf_path,
    citations_verified, citations_total, duration_seconds, notes, user_id
) VALUES (
    :session_id, :topic, :timestamp, :provider, :critic_score,
    :revision_count, :status, :saved_report_path, :docx_path, :pdf_path,
    :citations_verified, :citations_total, :duration_seconds, :notes, :user_id
);
"""

_SELECT_ALL_SQL = """
SELECT session_id, topic, timestamp, provider, critic_score,
       revision_count, status, saved_report_path,
       citations_verified, citations_total, duration_seconds, user_id
FROM research_sessions
ORDER BY timestamp DESC;
"""

_SELECT_BY_USER_SQL = """
SELECT session_id, topic, timestamp, provider, critic_score,
       revision_count, status, saved_report_path,
       citations_verified, citations_total, duration_seconds, user_id
FROM research_sessions
WHERE user_id = ?
ORDER BY timestamp DESC;
"""

_SELECT_ONE_SQL = """
SELECT * FROM research_sessions WHERE session_id = ?;
"""

_DELETE_ONE_SQL = """
DELETE FROM research_sessions WHERE session_id = ?;
"""

_DELETE_USER_SESSION_SQL = """
DELETE FROM research_sessions WHERE session_id = ? AND user_id = ?;
"""


# ─── Session Manager ──────────────────────────────────────────────────────────


class SessionManager:
    """
    Manages Research AI session persistence using SQLite.

    Parameters
    ----------
    db_path : str
        Path to the SQLite database file.
        Created automatically if it does not exist.
    """

    def __init__(self, db_path: str = _DEFAULT_DB_PATH) -> None:
        self.db_path = db_path
        self._init_db()

    # ── DB Lifecycle ──────────────────────────────────────────────

    def _init_db(self) -> None:
        """Create the database and table if they do not exist, and run migrations."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(_CREATE_TABLE_SQL)
            # Automatic schema migration: ensure user_id column exists
            cols = [col[1] for col in conn.execute("PRAGMA table_info(research_sessions)").fetchall()]
            if "user_id" not in cols:
                conn.execute("ALTER TABLE research_sessions ADD COLUMN user_id TEXT")

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager yielding a SQLite connection with auto-commit."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ── Save ──────────────────────────────────────────────────────

    def save_session(
        self,
        final_state: Dict[str, Any],
        duration_seconds: Optional[float] = None,
        notes: str = "",
        user_id: Optional[str] = None,
    ) -> str:
        """
        Persist a completed pipeline run to the database.

        Parameters
        ----------
        final_state      : dict  — The ``ResearchState`` dict returned by
                                   ``MultiAgentOrchestrator.run()``.
        duration_seconds : float — Wall-clock seconds the pipeline took.
        notes            : str   — Optional free-text annotation.
        user_id          : str   — Authenticated user's UUID.

        Returns
        -------
        str — The new ``session_id`` (UUID4).
        """
        session_id = str(uuid.uuid4())
        req_data = final_state.get("research_request") or {}
        topic = req_data.get("refined_query") or final_state.get("topic", "Unknown")

        # Extract citation stats from logs if not directly in state
        citations_verified = int(final_state.get("citations_verified", 0))
        citations_total = int(final_state.get("citations_total", 0))
        if not citations_total:
            for log in final_state.get("logs", []):
                if "Citations validated" in log:
                    import re
                    m = re.search(r"(\d+)/(\d+)", log)
                    if m:
                        citations_verified = int(m.group(1))
                        citations_total = int(m.group(2))
                    break

        # Extract docx / pdf paths from logs
        docx_path = pdf_path = ""
        for log in final_state.get("logs", []):
            if "DOCX saved" in log and not docx_path:
                import re
                m = re.search(r"DOCX saved to (.+)", log)
                if m:
                    docx_path = m.group(1).strip()
            if "PDF saved" in log and not pdf_path:
                import re
                m = re.search(r"PDF saved to (.+)", log)
                if m:
                    pdf_path = m.group(1).strip()

        row = {
            "session_id": session_id,
            "topic": topic,
            "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
            "provider": final_state.get("provider", "unknown"),
            "critic_score": final_state.get("critic_score"),
            "revision_count": final_state.get("revision_count", 0),
            "status": final_state.get("status", "unknown"),
            "saved_report_path": final_state.get("saved_report_path", ""),
            "docx_path": docx_path,
            "pdf_path": pdf_path,
            "citations_verified": citations_verified,
            "citations_total": citations_total,
            "duration_seconds": round(duration_seconds, 1) if duration_seconds else None,
            "notes": notes,
            "user_id": user_id,
        }

        with self._connect() as conn:
            conn.execute(_INSERT_SESSION_SQL, row)

        console.print(
            f"  [dim green]💾 Session saved to DB: [cyan]{session_id[:8]}...[/cyan] (user={user_id or 'anon'})[/dim green]"
        )
        return session_id

    # ── Retrieve ──────────────────────────────────────────────────

    def get_all_sessions(self, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return all sessions ordered newest-first. If user_id is provided, filter by user."""
        with self._connect() as conn:
            if user_id:
                rows = conn.execute(_SELECT_BY_USER_SQL, (user_id,)).fetchall()
            else:
                rows = conn.execute(_SELECT_ALL_SQL).fetchall()
        return [dict(r) for r in rows]

    def get_session(self, session_id: str, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Return a single session record by its full UUID, or None if not found or unauthorized."""
        with self._connect() as conn:
            row = conn.execute(_SELECT_ONE_SQL, (session_id,)).fetchone()
        if not row:
            return None
        rec = dict(row)
        if user_id and rec.get("user_id") and rec.get("user_id") != user_id:
            return None
        return rec

    def delete_session(self, session_id: str, user_id: Optional[str] = None) -> bool:
        """Delete a session record. Returns True if a row was deleted."""
        with self._connect() as conn:
            if user_id:
                cursor = conn.execute(_DELETE_USER_SESSION_SQL, (session_id, user_id))
            else:
                cursor = conn.execute(_DELETE_ONE_SQL, (session_id,))
        return cursor.rowcount > 0

    def user_owns_report(self, user_id: str, report_filename: str) -> bool:
        """
        Check whether an authenticated user owns a given report file (.md, .docx, .pdf).
        Returns True if the report was generated in a session owned by this user.
        """
        if not user_id or not report_filename:
            return False
        clean_name = Path(report_filename).name
        query = """
        SELECT 1 FROM research_sessions
        WHERE user_id = ? AND (
            saved_report_path LIKE ? OR
            docx_path LIKE ? OR
            pdf_path LIKE ?
        )
        LIMIT 1;
        """
        pattern = f"%{clean_name}"
        with self._connect() as conn:
            row = conn.execute(query, (user_id, pattern, pattern, pattern)).fetchone()
            if row:
                return True

            # If user has ANY session and the report was a pre-existing run with NULL user_id,
            # allow claiming or check if it matches
            legacy_check = """
            SELECT 1 FROM research_sessions
            WHERE user_id IS NULL AND (
                saved_report_path LIKE ? OR
                docx_path LIKE ? OR
                pdf_path LIKE ?
            )
            LIMIT 1;
            """
            legacy_row = conn.execute(legacy_check, (pattern, pattern, pattern)).fetchone()
            if legacy_row:
                # Associate the legacy session to this user so it's permanently owned
                conn.execute(
                    "UPDATE research_sessions SET user_id = ? WHERE user_id IS NULL AND (saved_report_path LIKE ? OR docx_path LIKE ? OR pdf_path LIKE ?)",
                    (user_id, pattern, pattern, pattern),
                )
                return True
        return False

    # ── Display ───────────────────────────────────────────────────

    def display_history(self, limit: Optional[int] = None) -> None:
        """
        Print all past research sessions as a Rich table.

        Parameters
        ----------
        limit : int, optional — Show only the most recent *N* sessions.
        """
        sessions = self.get_all_sessions()
        if limit:
            sessions = sessions[:limit]

        if not sessions:
            console.print(Panel(
                "No research sessions found in the database.\n"
                "[dim]Run a research query first to populate the history.[/dim]",
                title="📂 Research Session History",
                style="yellow",
            ))
            return

        table = Table(
            title=f"📂 Research Session History  ({len(sessions)} session(s))",
            show_lines=True,
            header_style="bold cyan",
        )
        table.add_column("#",             style="dim",         width=3,  no_wrap=True)
        table.add_column("Session ID",    style="dim cyan",    width=10, no_wrap=True)
        table.add_column("Timestamp",     style="white",       width=19, no_wrap=True)
        table.add_column("Topic",         style="bold white",  min_width=25, max_width=45, overflow="fold")
        table.add_column("Provider",      style="yellow",      width=10, no_wrap=True)
        table.add_column("Score",         style="green",       width=7,  no_wrap=True)
        table.add_column("Revisions",     style="cyan",        width=9,  no_wrap=True)
        table.add_column("Citations",     style="magenta",     width=11, no_wrap=True)
        table.add_column("Duration",      style="dim",         width=9,  no_wrap=True)
        table.add_column("Status",        width=12,            no_wrap=True)

        for i, s in enumerate(sessions, 1):
            score = s["critic_score"]
            score_str = f"{score}/10" if score is not None else "—"
            score_color = "green" if (score or 0) >= 8 else "yellow" if (score or 0) >= 6 else "red"

            status = s.get("status", "")
            if status == "completed":
                status_str = "[bold green]✅ done[/bold green]"
            elif status == "cancelled":
                status_str = "[yellow]⚠ cancelled[/yellow]"
            else:
                status_str = f"[dim]{status or '?'}[/dim]"

            rev = str(s.get("revision_count", 0))
            cit_v = s.get("citations_verified", 0)
            cit_t = s.get("citations_total", 0)
            cit_str = f"{cit_v}/{cit_t}" if cit_t else "—"

            dur = s.get("duration_seconds")
            dur_str = _format_duration(dur) if dur else "—"

            table.add_row(
                str(i),
                (s["session_id"] or "")[:8] + "…",
                s.get("timestamp", "")[:19],
                s.get("topic", "")[:45],
                s.get("provider", "—"),
                f"[{score_color}]{score_str}[/{score_color}]",
                rev,
                cit_str,
                dur_str,
                status_str,
            )

        console.print(Panel(table, style="blue"))
        console.print(
            f"  [dim]Database: [cyan]{self.db_path}[/cyan][/dim]"
        )

    def display_session_detail(self, session_id: str) -> None:
        """Print full details for a single session."""
        s = self.get_session(session_id)
        if not s:
            console.print(f"  [red]Session not found: {session_id}[/red]")
            return

        table = Table(show_header=False, box=None)
        table.add_column("Field", style="bold cyan", width=22)
        table.add_column("Value", style="white")

        for key, val in s.items():
            if val or val == 0:
                table.add_row(key.replace("_", " ").title(), str(val))

        console.print(Panel(
            table,
            title=f"🔍 Session Detail: {session_id[:16]}…",
            style="blue",
        ))


# ─── Timer Context Manager ────────────────────────────────────────────────────


class PipelineTimer:
    """
    Simple wall-clock timer for measuring pipeline duration.

    Usage::

        with PipelineTimer() as timer:
            result = orchestrator.run(topic)
        print(f"Took {timer.elapsed:.1f}s")
    """

    def __init__(self) -> None:
        self.start: float = 0.0
        self.elapsed: float = 0.0

    def __enter__(self) -> "PipelineTimer":
        self.start = time.perf_counter()
        return self

    def __exit__(self, *_) -> None:
        self.elapsed = time.perf_counter() - self.start


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _format_duration(seconds: float) -> str:
    """Return a human-friendly duration string: '2m 34s' or '45s'."""
    s = int(seconds)
    if s >= 60:
        return f"{s // 60}m {s % 60}s"
    return f"{s}s"


# ─── Standalone test ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        sm = SessionManager(db_path=f"{td}/test_sessions.db")

        # Simulate saving two sessions
        fake_state_1 = {
            "topic": "Quantum Computing in Drug Discovery",
            "research_request": {"refined_query": "Quantum algorithms for molecular simulation"},
            "provider": "gemini",
            "critic_score": 9,
            "revision_count": 1,
            "status": "completed",
            "saved_report_path": "/reports/report_20240101_quantum.md",
            "logs": [
                "Citations validated: 12/14 live (86%)",
                "DOCX saved to report_20240101_quantum.docx",
                "PDF saved to report_20240101_quantum.pdf",
            ],
        }
        fake_state_2 = {
            "topic": "LLM Scaling Laws",
            "research_request": {"refined_query": "Empirical scaling laws for language models"},
            "provider": "mistral",
            "critic_score": 7,
            "revision_count": 2,
            "status": "completed",
            "saved_report_path": "/reports/report_20240102_llm.md",
            "logs": ["Citations validated: 8/10 live (80%)"],
        }

        sm.save_session(fake_state_1, duration_seconds=134.5)
        sm.save_session(fake_state_2, duration_seconds=212.0, notes="Trial run")

        sm.display_history()
