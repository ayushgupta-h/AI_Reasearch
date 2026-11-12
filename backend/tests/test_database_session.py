"""
tests/test_database_session.py
==============================
Tests for the engine/session layer. None of these touch a live database.
"""

import pytest
from sqlalchemy.engine import Engine

from app.config import Settings
from app.database.session import (
    build_engine,
    check_connection,
    get_db,
    get_engine,
    reset_engine,
)


def test_build_engine_requires_url():
    with pytest.raises(ValueError):
        build_engine("")


def test_build_engine_rejects_non_postgres():
    with pytest.raises(ValueError):
        build_engine("sqlite:///nope.db")


def test_build_engine_returns_engine():
    engine = build_engine("postgresql://user:pass@localhost:5432/db")
    assert isinstance(engine, Engine)


def test_get_engine_without_url_raises():
    reset_engine()
    with pytest.raises(RuntimeError):
        get_engine(Settings())  # database_url defaults to ""


def test_check_connection_false_without_url():
    reset_engine()
    assert check_connection(Settings()) is False


def test_get_db_requires_configured_url(monkeypatch):
    reset_engine()
    # Force an unconfigured settings so the test is independent of any real
    # DATABASE_URL present in the environment / .env.
    monkeypatch.setattr(
        "app.database.session.get_settings", lambda: Settings()
    )
    with pytest.raises(RuntimeError):
        next(get_db())


def test_reset_engine_is_idempotent():
    reset_engine()
    reset_engine()  # should not raise


def test_settings_database_url_is_repr_safe():
    settings = Settings(database_url="postgresql://user:secret@host/db")
    assert "secret" not in repr(settings)
    assert settings.database_url.startswith("postgresql://")
