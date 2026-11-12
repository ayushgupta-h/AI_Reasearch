"""add durable research event stream tables

Revision ID: 0002_research_event_stream
Revises: 0001_initial
Create Date: 2026-11-11

The baseline migration builds from ORM metadata, so a fresh database already
gets these tables. This guarded migration adds them to databases that were
initialised before TASK 2.5 without failing on a fresh install.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision: str = "0002_research_event_stream"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables(bind) -> set[str]:
    return set(inspect(bind).get_table_names())


def upgrade() -> None:
    bind = op.get_bind()
    existing = _tables(bind)

    if "research_event_log" not in existing:
        op.create_table(
            "research_event_log",
            sa.Column("event_id", sa.String(length=64), primary_key=True),
            sa.Column("research_id", sa.String(length=64), nullable=False),
            sa.Column("user_id", sa.String(length=128), nullable=False),
            sa.Column("event_type", sa.String(length=64), nullable=False),
            sa.Column("sequence_number", sa.BigInteger(), nullable=False),
            sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("payload", sa.Text(), nullable=False),
        )
        op.create_index(
            "ix_research_event_log_research_id",
            "research_event_log",
            ["research_id"],
        )
        op.create_index(
            "ix_research_event_log_event_type",
            "research_event_log",
            ["event_type"],
        )
        op.create_index(
            "ix_research_event_log_research_sequence",
            "research_event_log",
            ["research_id", "sequence_number"],
        )
        op.create_index(
            "ix_research_event_log_owner",
            "research_event_log",
            ["user_id", "occurred_at"],
        )

    if "research_run_status" not in existing:
        op.create_table(
            "research_run_status",
            sa.Column("research_id", sa.String(length=64), primary_key=True),
            sa.Column("owner_id", sa.String(length=128), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index(
            "ix_research_run_status_owner_id",
            "research_run_status",
            ["owner_id"],
        )
        op.create_index(
            "ix_research_run_status_owner",
            "research_run_status",
            ["owner_id", "updated_at"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    existing = _tables(bind)
    if "research_event_log" in existing:
        op.drop_table("research_event_log")
    if "research_run_status" in existing:
        op.drop_table("research_run_status")
