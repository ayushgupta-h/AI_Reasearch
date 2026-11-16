"""add research completeness objectives and evaluations tables

Revision ID: 0007_research_completeness
Revises: 0006_human_review
Create Date: 2026-11-12

Adds the ``research_objectives`` and ``research_completeness_evaluations``
tables used by the research completeness evaluator (Task 3.7).

* ``research_objectives`` — decomposed sub-questions derived from the
  original research question, each with a coverage status and links to
  related claims/sources.
* ``research_completeness_evaluations`` — snapshot of one evaluation run,
  including the per-objective rollup, identified gaps, and bounded
  additional-research recommendations.

Both tables are scoped by ``session_id`` (foreign key to
``research_sessions.id``) so one user's evaluation can never leak into
another's session. All columns are created guarded so the migration is safe
on both fresh and pre-existing databases.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = "0007_research_completeness"
down_revision: Union[str, None] = "0006_human_review"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables(bind) -> set[str]:
    return set(inspect(bind).get_table_names())


def upgrade() -> None:
    bind = op.get_bind()
    existing = _tables(bind)

    if "research_objectives" not in existing:
        op.create_table(
            "research_objectives",
            sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
            sa.Column(
                "session_id",
                sa.Uuid(as_uuid=True),
                sa.ForeignKey("research_sessions.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("objective_id", sa.String(length=40), nullable=False, index=True),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("required_evidence", sa.Text(), nullable=True),
            sa.Column(
                "status",
                sa.String(length=30),
                nullable=False,
                server_default="not_investigated",
            ),
            sa.Column("related_claim_ids", sa.Text(), nullable=True),
            sa.Column("related_source_ids", sa.Text(), nullable=True),
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
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_research_objectives_session",
            "research_objectives",
            ["session_id"],
        )
        op.create_index(
            "ix_research_objectives_session_status",
            "research_objectives",
            ["session_id", "status"],
        )

    if "research_completeness_evaluations" not in existing:
        op.create_table(
            "research_completeness_evaluations",
            sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
            sa.Column(
                "session_id",
                sa.Uuid(as_uuid=True),
                sa.ForeignKey("research_sessions.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column(
                "overall_status",
                sa.String(length=30),
                nullable=False,
                server_default="insufficient",
            ),
            sa.Column(
                "completeness_score",
                sa.Float(),
                nullable=False,
                server_default="0.0",
            ),
            sa.Column("total_objectives", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("complete_objectives", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("partial_objectives", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("insufficient_objectives", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("not_investigated_objectives", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("blocked_objectives", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("gaps_json", sa.Text(), nullable=True),
            sa.Column("recommendations_json", sa.Text(), nullable=True),
            sa.Column(
                "additional_search_tasks_json",
                sa.Text(),
                nullable=True,
            ),
            sa.Column(
                "iterations_performed",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
            sa.Column(
                "max_iterations",
                sa.Integer(),
                nullable=False,
                server_default="0",
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
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_completeness_evals_session",
            "research_completeness_evaluations",
            ["session_id"],
        )
        op.create_index(
            "ix_completeness_evals_session_created",
            "research_completeness_evaluations",
            ["session_id", "created_at"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    existing = _tables(bind)
    if "research_completeness_evaluations" in existing:
        op.drop_table("research_completeness_evaluations")
    if "research_objectives" in existing:
        op.drop_table("research_objectives")
