"""PostgreSQL tables for durable research event transport (TASK 2.5)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import BigInteger, DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models.base import Base


class ResearchEventRecord(Base):
    """One audit/replay event written by a research worker.

    The identifiers are stored as strings because the Celery task id and the
    Supabase owner ``sub`` are not always UUID-typed in local deployments.
    Redis remains the ordered live transport; this table is the durable journal.
    """

    __tablename__ = "research_event_log"
    # A best-effort journal entry made while Redis is unavailable has
    # sequence_number=0, so a unique (research_id, sequence_number) constraint
    # would drop every fallback event after the first. Streamed events are
    # already serialised by the Redis publisher lock; the index keeps replay
    # ordered without rejecting fallback rows.
    __table_args__ = (
        Index("ix_research_event_log_research_sequence", "research_id", "sequence_number"),
        Index("ix_research_event_log_owner", "user_id", "occurred_at"),
    )

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    research_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    sequence_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    payload: Mapped[str] = mapped_column(Text, nullable=False, default="{}")


class ResearchRunStatus(Base):
    """Authoritative distributed run status for API authorisation/status reads."""

    __tablename__ = "research_run_status"
    __table_args__ = (Index("ix_research_run_status_owner", "owner_id", "updated_at"),)

    research_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


__all__ = ["ResearchEventRecord", "ResearchRunStatus"]
