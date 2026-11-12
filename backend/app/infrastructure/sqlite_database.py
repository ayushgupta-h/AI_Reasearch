"""
app/infrastructure/sqlite_database.py
=====================================
Thin SQL adapter over the existing SQLite file.

The application's session persistence still lives in
:class:`session_manager.SessionManager`; this adapter is the general-purpose
``Database`` port for future tables and for code that needs transactional SQL
without importing :mod:`sqlite3` itself. Both use the same database file.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional, Sequence

from app.errors import DatabaseError


class _SQLiteTransaction:
    """Statement helpers bound to one open transaction."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        return self._connection.execute(sql, tuple(params)).rowcount

    def fetch_one(
        self, sql: str, params: Sequence[Any] = ()
    ) -> Optional[Mapping[str, Any]]:
        row = self._connection.execute(sql, tuple(params)).fetchone()
        return dict(row) if row is not None else None

    def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list[Mapping[str, Any]]:
        rows = self._connection.execute(sql, tuple(params)).fetchall()
        return [dict(row) for row in rows]


class SQLiteDatabase:
    """Per-operation SQLite connections with consistent error translation."""

    def __init__(self, path: str | Path, *, timeout: float = 5.0) -> None:
        self._path = Path(path)
        self._timeout = timeout

    @property
    def path(self) -> Path:
        return self._path

    # ── Database ──────────────────────────────────────────────────────────

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        try:
            with self._connection() as connection:
                with connection:
                    return connection.execute(sql, tuple(params)).rowcount
        except sqlite3.Error as exc:
            raise DatabaseError("The application database operation failed.") from exc

    def executemany(self, sql: str, rows: Sequence[Sequence[Any]]) -> int:
        try:
            with self._connection() as connection:
                with connection:
                    cursor = connection.executemany(sql, [tuple(row) for row in rows])
                    return cursor.rowcount
        except sqlite3.Error as exc:
            raise DatabaseError("The application database operation failed.") from exc

    def fetch_one(
        self, sql: str, params: Sequence[Any] = ()
    ) -> Optional[Mapping[str, Any]]:
        try:
            with self._connection() as connection:
                row = connection.execute(sql, tuple(params)).fetchone()
                return dict(row) if row is not None else None
        except sqlite3.Error as exc:
            raise DatabaseError("The application database operation failed.") from exc

    def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list[Mapping[str, Any]]:
        try:
            with self._connection() as connection:
                rows = connection.execute(sql, tuple(params)).fetchall()
                return [dict(row) for row in rows]
        except sqlite3.Error as exc:
            raise DatabaseError("The application database operation failed.") from exc

    @contextmanager
    def transaction(self) -> Iterator[_SQLiteTransaction]:
        connection = self._connect()
        try:
            connection.execute("BEGIN")
            transaction = _SQLiteTransaction(connection)
            try:
                yield transaction
            except BaseException:
                connection.rollback()
                raise
            else:
                connection.commit()
        except sqlite3.Error as exc:
            raise DatabaseError("The application database operation failed.") from exc
        finally:
            connection.close()

    def healthcheck(self) -> bool:
        try:
            with self._connection() as connection:
                connection.execute("SELECT 1").fetchone()
            return True
        except sqlite3.Error:
            return False

    # ── Internals ─────────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self._path), timeout=self._timeout)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()


__all__ = ["SQLiteDatabase"]
