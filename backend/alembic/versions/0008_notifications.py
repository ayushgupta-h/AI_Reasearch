"""add notifications table for user-facing notification system

Revision ID: 0008_notifications
Revises: 0007_research_completeness
Create Date: 2026-11-12

Adds the ``notifications`` table used by the research notification system
(Task 4.7). Each row is owned by a user (``owner_id`` foreign key to
``users.id``) and tracks a notification category, title, body, optional JSON
payload, read/unread state and read timestamp.

Both read state and creation time are indexed so the unread-count and
list-notifications queries are efficient. All columns are created guarded so
the migration is safe on both fresh and pre-existing databases.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = "0008_notifications"
down_revision: Union[str, None] = "0007_research_completeness"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables(bind) -> set[str]:
    return set(inspect(bind).get_table_names())


def upgrade() -> None:
    bind = op.get_bind()
    existing = _tables(bind)

    if "notifications" not in existing:
        op.create_table(
            "notifications",
            sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
            sa.Column(
                "owner_id",
                sa.Uuid(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column(
                "category",
                sa.String(length=40),
                nullable=False,
                index=True,
            ),
            sa.Column("title", sa.String(length=200), nullable=False),
            sa.Column("body", sa.Text(), nullable=True),
            sa.Column("data_json", sa.Text(), nullable=True),
            sa.Column(
                "read",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
                index=True,
            ),
            sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
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
            "ix_notifications_owner_read_created",
            "notifications",
            ["owner_id", "read", "created_at"],
        )
        op.create_index(
            "ix_notifications_owner_category",
            "notifications",
            ["owner_id", "category"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    existing = _tables(bind)
    if "notifications" in existing:
        op.drop_table("notifications")
