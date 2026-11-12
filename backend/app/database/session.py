"""
app/database/session.py
=======================
PostgreSQL engine and session management built on SQLAlchemy 2.x with
Psycopg 3.

Design
------
* The engine is created lazily from ``settings.database_url`` (the value of
  the ``DATABASE_URL`` / ``DB_URL`` environment variable). The URL is never
  logged and never printed.
* Connection pooling uses ``pool_pre_ping`` so stale connections from a
  Supabase-side restart are transparently re-established.
* ``get_db()`` is the FastAPI dependency yielding a ``Session`` with commit on
  success and rollback on error.
* Nothing here imports ``psycopg`` directly; SQLAlchemy loads the dialect only
  when an engine is actually created, so the app still starts when PostgreSQL
  has not been provisioned.
"""

from __future__ import annotations

from typing import Iterator, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.database.models.base import Base

_engine: Optional[Engine] = None
_session_factory: Optional[sessionmaker] = None


def _normalize_url(url: str) -> str:
    """Force the Psycopg 3 dialect for a bare ``postgresql://`` URL.

    SQLAlchemy loads ``psycopg2`` by default for a plain ``postgresql://``
    scheme; this project standardises on Psycopg 3, so we rewrite the scheme
    to ``postgresql+psycopg://`` unless the caller already pinned a driver.
    """
    if url.startswith("postgresql+psycopg://"):
        return url
    if url.startswith("postgresql://"):
        return "postgresql+psycopg" + url[len("postgresql"):]
    return url


def build_engine(url: str, *, pool_size: int = 5, max_overflow: int = 10) -> Engine:
    """Create a pooled SQLAlchemy engine for a PostgreSQL URL."""
    if not url:
        raise ValueError("A database URL is required to build a PostgreSQL engine.")
    if not url.startswith("postgresql"):
        raise ValueError("DATABASE_URL must be a postgresql:// connection string.")

    return create_engine(
        _normalize_url(url),
        pool_pre_ping=True,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_recycle=1800,
        echo=False,
    )


def get_engine(settings: Optional[Settings] = None) -> Engine:
    """Return the process-wide engine, building it on first use."""
    global _engine
    if _engine is None:
        cfg = settings or get_settings()
        if not cfg.database_url:
            raise RuntimeError(
                "DATABASE_URL is not configured. Set it in the environment or in "
                "backend/.env before using PostgreSQL persistence."
            )
        _engine = build_engine(cfg.database_url)
    return _engine


def get_session_factory(settings: Optional[Settings] = None) -> sessionmaker:
    """Return a configured ``sessionmaker`` bound to the shared engine."""
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=get_engine(settings),
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
            class_=Session,
        )
    return _session_factory


def reset_engine() -> None:
    """Drop cached engine/factory (used by tests and config reloads)."""
    global _engine, _session_factory
    _engine = None
    _session_factory = None


def get_db() -> Iterator[Session]:
    """FastAPI dependency: yield a transactional session, commit/rollback.

    This is the only recommended way for application code to obtain a session;
    it guarantees the transaction is either committed or rolled back.
    """
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def check_connection(settings: Optional[Settings] = None) -> bool:
    """Best-effort connectivity probe that never raises and never prints.

    Returns ``True`` when ``SELECT 1`` succeeds, ``False`` when PostgreSQL is
    unreachable, and ``False`` when no ``DATABASE_URL`` is configured.
    """
    try:
        engine = get_engine(settings)
    except Exception:
        return False
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def create_schema(settings: Optional[Settings] = None) -> None:
    """Create all tables (used by tests / bootstrap; production uses Alembic)."""
    engine = get_engine(settings)
    Base.metadata.create_all(engine)


__all__ = [
    "build_engine",
    "check_connection",
    "create_schema",
    "get_db",
    "get_engine",
    "get_session_factory",
    "reset_engine",
]
