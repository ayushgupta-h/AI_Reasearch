"""add human plan/report review state and audit history

Revision ID: 0006_human_review
Revises: 0005_contradiction_analysis, 0005_research_memory
Create Date: 2026-11-12
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision: str = "0006_human_review"
down_revision: Union[str, Sequence[str], None] = (
    "0005_contradiction_analysis",
    "0005_research_memory",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables(bind) -> set[str]:
    return set(inspect(bind).get_table_names())


def _columns(bind, table: str) -> set[str]:
    return {column["name"] for column in inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    tables = _tables(bind)

    if "research_plans" in tables:
        columns = _columns(bind, "research_plans")
        if "reviewed_by" not in columns:
            op.add_column(
                "research_plans",
                sa.Column("reviewed_by", sa.String(length=128), nullable=True),
            )
        if "reviewed_at" not in columns:
            op.add_column(
                "research_plans",
                sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
            )
        if "review_note" not in columns:
            op.add_column(
                "research_plans",
                sa.Column("review_note", sa.Text(), nullable=True),
            )
        if "details_json" not in columns:
            op.add_column(
                "research_plans",
                sa.Column("details_json", sa.Text(), nullable=True),
            )

    if "review_requests" in tables:
        columns = _columns(bind, "review_requests")
        if "previous_status" not in columns:
            op.add_column(
                "review_requests",
                sa.Column("previous_status", sa.String(length=30), nullable=True),
            )

    if "review_comments" in tables:
        columns = _columns(bind, "review_comments")
        if "kind" not in columns:
            op.add_column(
                "review_comments",
                sa.Column(
                    "kind",
                    sa.String(length=30),
                    nullable=False,
                    server_default="general",
                ),
            )
        if "claim_ids_json" not in columns:
            op.add_column(
                "review_comments",
                sa.Column("claim_ids_json", sa.Text(), nullable=True),
            )

    if "review_audits" not in tables:
        op.create_table(
            "review_audits",
            sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
            sa.Column(
                "session_id",
                sa.Uuid(as_uuid=True),
                sa.ForeignKey("research_sessions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "plan_id",
                sa.Uuid(as_uuid=True),
                sa.ForeignKey("research_plans.id", ondelete="CASCADE"),
                nullable=True,
            ),
            sa.Column(
                "report_id",
                sa.Uuid(as_uuid=True),
                sa.ForeignKey("research_reports.id", ondelete="CASCADE"),
                nullable=True,
            ),
            sa.Column("reviewer_id", sa.String(length=128), nullable=False),
            sa.Column("action", sa.String(length=50), nullable=False),
            sa.Column("previous_status", sa.String(length=30), nullable=True),
            sa.Column("new_status", sa.String(length=30), nullable=False),
            sa.Column("comment", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
        op.create_index(
            "ix_review_audit_session",
            "review_audits",
            ["session_id", "created_at"],
        )
        op.create_index(
            "ix_review_audit_report",
            "review_audits",
            ["report_id", "created_at"],
        )
        op.create_index(
            "ix_review_audit_plan",
            "review_audits",
            ["plan_id", "created_at"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    tables = _tables(bind)
    if "review_audits" in tables:
        op.drop_table("review_audits")
    if "review_comments" in tables:
        columns = _columns(bind, "review_comments")
        if "claim_ids_json" in columns:
            op.drop_column("review_comments", "claim_ids_json")
        if "kind" in columns:
            op.drop_column("review_comments", "kind")
    if "review_requests" in tables:
        columns = _columns(bind, "review_requests")
        if "previous_status" in columns:
            op.drop_column("review_requests", "previous_status")
    if "research_plans" in tables:
        columns = _columns(bind, "research_plans")
        if "details_json" in columns:
            op.drop_column("research_plans", "details_json")
        if "review_note" in columns:
            op.drop_column("research_plans", "review_note")
        if "reviewed_at" in columns:
            op.drop_column("research_plans", "reviewed_at")
        if "reviewed_by" in columns:
            op.drop_column("research_plans", "reviewed_by")
