"""add research_memory table for persistent research memory

Revision ID: 0005_research_memory
Revises: 0004_source_credibility
Create Date: 2026-11-12

Adds the ``research_memory`` table used by the persistent research memory
feature (Task 3.5). Memory records are scoped to a user (required) and
optionally narrowed to a project and/or research session, enforcing
multi-tenant isolation at the storage boundary.

The table is created guarded (``IF NOT EXISTS`` logic via inspect) so the
migration is safe on both fresh and pre-existing databases.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = "0005_research_memory"
down_revision: Union[str, None] = "0004_source_credibility"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables(bind) -> set[str]:
    return set(inspect(bind).get_table_names())


def upgrade() -> None:
    bind = op.get_bind()
    existing = _tables(bind)

    if "research_memory" not in existing:
        op.create_table(
            "research_memory",
            sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
            sa.Column(
                "user_id",
                sa.Uuid(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column(
                "project_id",
                sa.Uuid(as_uuid=True),
                sa.ForeignKey("projects.id", ondelete="CASCADE"),
                nullable=True,
                index=True,
            ),
            sa.Column(
                "session_id",
                sa.Uuid(as_uuid=True),
                sa.ForeignKey("research_sessions.id", ondelete="CASCADE"),
                nullable=True,
                index=True,
            ),
            sa.Column(
                "source_id",
                sa.Uuid(as_uuid=True),
                sa.ForeignKey("research_sources.id", ondelete="CASCADE"),
                nullable=True,
            ),
            sa.Column("content_hash", sa.String(length=64), nullable=False, index=True),
            sa.Column(
                "content_type",
                sa.String(length=40),
                nullable=False,
                server_default="finding",
                index=True,
            ),
            sa.Column("content_text", sa.Text(), nullable=False),
            sa.Column(
                "verification_status",
                sa.String(length=30),
                nullable=False,
                server_default="unverified",
                index=True,
            ),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("chroma_id", sa.String(length=255), nullable=True, unique=True, index=True),
            sa.Column("metadata_json", sa.Text(), nullable=True),
            sa.Column(
                "deleted",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            sa.Column(
                "deleted_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.UniqueConstraint(
                "user_id",
                "project_id",
                "session_id",
                "content_hash",
                name="uq_research_memory_dedupe",
            ),
        )
        op.create_index(
            "ix_research_memory_user_created",
            "research_memory",
            ["user_id", "created_at"],
        )
        op.create_index(
            "ix_research_memory_project",
            "research_memory",
            ["project_id"],
        )
        op.create_index(
            "ix_research_memory_session",
            "research_memory",
            ["session_id"],
        )
        op.create_index(
            "ix_research_memory_verification",
            "research_memory",
            ["verification_status"],
        )
        op.create_index(
            "ix_research_memory_content_type",
            "research_memory",
            ["content_type"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    existing = _tables(bind)
    if "research_memory" in existing:
        op.drop_table("research_memory")
