"""initial PostgreSQL schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-01-01

This baseline migration materialises the full schema from the SQLAlchemy
models (``app.database.models``). Creating from ``Base.metadata`` guarantees
the migration can never drift from the ORM definitions; follow-on migrations
should use explicit ``op.create_table`` / ``op.add_column`` for incremental
changes.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.database.models.base import Base
from app.database.models import *  # noqa: E402,F401,F403  (register all tables)

# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create every table from the ORM metadata (user, project, sessions, plans,
    # tasks, executions, sources, claims, evidence, contradictions, reports,
    # versions, reviews, comments, documents, audit logs, model usage).
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
