"""add source credibility evaluations

Revision ID: 0004_source_credibility
Revises: 0003_langgraph_checkpoints
Create Date: 2026-11-12

The baseline migration creates tables from ORM metadata. This guarded migration
adds the credibility table to databases initialized before Task 3.3 while
remaining safe for fresh installs.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision: str = "0004_source_credibility"
down_revision: Union[str, None] = "0003_langgraph_checkpoints"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if "source_credibility_evaluations" in set(inspect(bind).get_table_names()):
        return

    op.create_table(
        "source_credibility_evaluations",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "source_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("research_sources.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("research_id", sa.String(length=64), nullable=True),
        sa.Column("source_url", sa.String(length=2048), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("publisher", sa.String(length=200), nullable=True),
        sa.Column("source_type", sa.String(length=40), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column(
            "label",
            sa.String(length=40),
            nullable=False,
            server_default="insufficient_information",
        ),
        sa.Column("factors_json", sa.Text(), nullable=False),
        sa.Column("missing_fields_json", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column(
            "method",
            sa.String(length=40),
            nullable=False,
            server_default="heuristic_v1",
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
            "research_id",
            "source_url",
            name="uq_source_credibility_evaluation_run_url",
        ),
    )
    op.create_index(
        "ix_source_credibility_evaluations_created_at",
        "source_credibility_evaluations",
        ["created_at"],
    )
    op.create_index(
        "ix_source_credibility_evaluations_research",
        "source_credibility_evaluations",
        ["research_id"],
    )
    op.create_index(
        "ix_source_credibility_evaluations_source",
        "source_credibility_evaluations",
        ["source_id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    if "source_credibility_evaluations" in set(inspect(bind).get_table_names()):
        op.drop_table("source_credibility_evaluations")
