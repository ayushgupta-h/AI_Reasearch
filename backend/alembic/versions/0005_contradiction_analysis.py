"""add explainable contradiction categories and evidence links

Revision ID: 0005_contradiction_analysis
Revises: 0004_source_credibility
Create Date: 2026-11-12

The existing research_contradictions table already stores the two claim IDs and
an explanation. This migration adds the classification/category metadata and a
side-aware evidence association so both sides remain inspectable.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision: str = "0005_contradiction_analysis"
down_revision: Union[str, None] = "0004_source_credibility"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables(bind) -> set[str]:
    return set(inspect(bind).get_table_names())


def _columns(bind, table: str) -> set[str]:
    return {column["name"] for column in inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    existing_tables = _tables(bind)

    if "research_contradictions" in existing_tables:
        columns = _columns(bind, "research_contradictions")
        if "category" not in columns:
            op.add_column(
                "research_contradictions",
                sa.Column(
                    "category",
                    sa.String(length=40),
                    nullable=False,
                    server_default="insufficient_information",
                ),
            )
        if "analysis_json" not in columns:
            op.add_column(
                "research_contradictions",
                sa.Column("analysis_json", sa.Text(), nullable=True),
            )

    if "contradiction_evidence" not in existing_tables:
        op.create_table(
            "contradiction_evidence",
            sa.Column(
                "contradiction_id",
                sa.Uuid(as_uuid=True),
                sa.ForeignKey("research_contradictions.id", ondelete="CASCADE"),
                primary_key=True,
                nullable=False,
            ),
            sa.Column(
                "evidence_id",
                sa.Uuid(as_uuid=True),
                sa.ForeignKey("research_evidence.id", ondelete="CASCADE"),
                primary_key=True,
                nullable=False,
            ),
            sa.Column(
                "side",
                sa.String(length=1),
                nullable=False,
                server_default="a",
            ),
            sa.Column("weight", sa.Float(), nullable=False, server_default="1"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.UniqueConstraint(
                "contradiction_id",
                "evidence_id",
                name="uq_contradiction_evidence",
            ),
        )
        op.create_index(
            "ix_contradiction_evidence_evidence",
            "contradiction_evidence",
            ["evidence_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    existing_tables = _tables(bind)
    if "contradiction_evidence" in existing_tables:
        op.drop_table("contradiction_evidence")
    if "research_contradictions" in existing_tables:
        columns = _columns(bind, "research_contradictions")
        if "analysis_json" in columns:
            op.drop_column("research_contradictions", "analysis_json")
        if "category" in columns:
            op.drop_column("research_contradictions", "category")
