"""
app/database/models/notifications.py
====================================
PostgreSQL models for user-facing notifications.

Stores one row per notification event targeted at an authenticated user.
Notifications carry a category (research_queued, document_processing_failed,
etc.), a read/unread status, and timestamps. All rows are owner-scoped so one
user's notifications can never leak into another's.

The table is intentionally lightweight: it records the notification payload
and read state. Live delivery (if a Redis/SSE transport is configured) is
handled by the notification service, which publishes events to the existing
event bus after persisting.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import Base, fk_uuid, pk_uuid, TimestampMixin


class Notification(Base, TimestampMixin):
    """One user-facing notification.

    ``owner_id`` is the Supabase Auth ``sub`` from the validated JWT — it is
    set server-side from the authenticated identity, never accepted from the
    request body. ``read_at`` is null until the notification is marked read.
    """

    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_owner_read_created", "owner_id", "read", "created_at"),
        Index("ix_notifications_owner_category", "owner_id", "category"),
    )

    id: Mapped[uuid.UUID] = pk_uuid()
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    category: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Free-form JSON payload for client-side rendering / deep links.
    data_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    read: Mapped[bool] = mapped_column(default=False, nullable=False, index=True)
    read_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)

    user: Mapped["User"] = relationship(back_populates="notifications")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Notification id={self.id} category={self.category} read={self.read}>"


__all__ = ["Notification"]
