"""
app/services/session_service.py
===============================
Business-logic wrapper around the existing SQLite ``SessionManager``.

The persistence implementation is deliberately reused (no second database
layer). This service adds:

* consistent error translation (``sqlite3.Error`` -> ``DatabaseError``),
* a single injected instance instead of ad-hoc ``SessionManager()`` calls,
* structured logging around persistence operations.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional

from app.errors import DatabaseError
from app.observability import get_logger
from session_manager import SessionManager

logger = get_logger("app.services.sessions")


class SessionService:
    """Injected facade over :class:`session_manager.SessionManager`."""

    def __init__(self, manager: SessionManager) -> None:
        self._manager = manager

    @property
    def manager(self) -> SessionManager:
        return self._manager

    def list_sessions(self, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        try:
            return self._manager.get_all_sessions(user_id=user_id)
        except sqlite3.Error as exc:
            logger.error("session.list_failed", extra={"error_code": "database_unavailable"})
            raise DatabaseError("Could not load research sessions.") from exc

    def get_session(
        self, session_id: str, user_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        try:
            return self._manager.get_session(session_id, user_id=user_id)
        except sqlite3.Error as exc:
            logger.error("session.get_failed", extra={"error_code": "database_unavailable"})
            raise DatabaseError("Could not load the research session.") from exc

    def delete_session(self, session_id: str, user_id: Optional[str]) -> bool:
        try:
            return self._manager.delete_session(session_id, user_id=user_id)
        except sqlite3.Error as exc:
            logger.error("session.delete_failed", extra={"error_code": "database_unavailable"})
            raise DatabaseError("Could not delete the research session.") from exc

    def save_session(
        self,
        state: Dict[str, Any],
        *,
        duration_seconds: Optional[float] = None,
        notes: str = "",
        user_id: Optional[str] = None,
    ) -> str:
        try:
            session_id = self._manager.save_session(
                state,
                duration_seconds=duration_seconds,
                notes=notes,
                user_id=user_id,
            )
        except sqlite3.Error as exc:
            logger.error("session.save_failed", extra={"error_code": "database_unavailable"})
            raise DatabaseError("Could not persist the research session.") from exc
        logger.info("session.saved", extra={"session_id": session_id})
        return session_id

    def user_owns_report(self, user_id: str, filename: str) -> bool:
        try:
            return self._manager.user_owns_report(user_id, filename)
        except sqlite3.Error as exc:
            logger.error(
                "session.ownership_check_failed", extra={"error_code": "database_unavailable"}
            )
            raise DatabaseError("Could not verify report ownership.") from exc


def build_session_service(db_path: Optional[str] = None) -> SessionService:
    return SessionService(SessionManager(db_path) if db_path else SessionManager())
