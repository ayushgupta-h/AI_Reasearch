"""
app/repositories/postgres
=========================
SQLAlchemy repository implementations for the PostgreSQL layer.

These complement the existing SQL-scoped ``ProjectRepository`` etc. (which
target SQLite). They are not swapped in yet — the composition root still wires
SQLite adapters so existing functionality is preserved. See
``docs/POSTGRES_MIGRATION.md`` for the cutover plan.
"""

from __future__ import annotations

from app.repositories.postgres.base import PostgresRepository

from app.database.models.domain import Project, ResearchSession, User
from app.database.models.report import ResearchReport, ReviewRequest

__all__ = [
    "PostgresRepository",
    "Project",
    "ResearchReport",
    "ResearchSession",
    "ReviewRequest",
    "User",
]
