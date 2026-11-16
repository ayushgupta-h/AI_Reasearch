"""add langgraph checkpoint table for durable research orchestration

Revision ID: 0003_langgraph_checkpoints
Revises: 0002_research_event_stream
Create Date: 2026-11-12

Adds the ``langgraph_checkpoints`` table used by ``PostgresCheckpointer`` to
persist LangGraph workflow state between nodes. All rows carry ``owner_id``
and every query is scoped by it, enforcing multi-tenant isolation: one user's
workflow can never read another user's checkpoints.

The table is created guarded (``IF NOT EXISTS``) so the migration is safe on
both fresh and pre-existing databases.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = "0003_langgraph_checkpoints"
down_revision: Union[str, None] = "0002_research_event_stream"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables(bind) -> set[str]:
    return set(inspect(bind).get_table_names())


def upgrade() -> None:
    bind = op.get_bind()
    existing = _tables(bind)

    if "langgraph_checkpoints" not in existing:
        op.create_table(
            "langgraph_checkpoints",
            sa.Column("thread_id", sa.String(length=255), nullable=False),
            sa.Column("checkpoint_ns", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("checkpoint_id", sa.String(length=255), nullable=False),
            sa.Column("parent_checkpoint_id", sa.String(length=255), nullable=True),
            sa.Column("type", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("checkpoint", sa.JSON(), nullable=False),
            sa.Column("metadata", sa.JSON(), nullable=False),
            sa.Column("owner_id", sa.String(length=255), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("NOW()"),
            ),
            sa.PrimaryKeyConstraint(
                "thread_id", "checkpoint_ns", "checkpoint_id",
                name="pk_langgraph_checkpoints",
            ),
        )
        op.create_index(
            "ix_langgraph_checkpoints_owner",
            "langgraph_checkpoints",
            ["owner_id", "created_at"],
        )
        op.create_index(
            "ix_langgraph_checkpoints_thread",
            "langgraph_checkpoints",
            ["thread_id"],
        )
        op.create_index(
            "ix_langgraph_checkpoints_owner_thread",
            "langgraph_checkpoints",
            ["owner_id", "thread_id", "created_at"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    existing = _tables(bind)
    if "langgraph_checkpoints" in existing:
        op.drop_table("langgraph_checkpoints")
