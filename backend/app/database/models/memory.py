"""
app/database/models/memory.py
==============================
Persistent research memory model.

ResearchMemory captures verified findings, evidence excerpts and claim
summaries from completed research sessions so they can be reused across
future research. Every record is scoped to a user (and optionally a project
and research session) so memory never leaks across ownership boundaries.

Vector embeddings for semantic retrieval live in the existing ChromaDB
instance; this table is the authoritative metadata store.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import Float, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import (
    Base,
    SoftDeleteMixin,
    TimestampMixin,
    fk_uuid,
    pk_uuid,
)


class ResearchMemory(Base, TimestampMixin, SoftDeleteMixin):
    """One durable memory record from a research session.

    ``content_text`` holds the human-readable summary/finding. ``content_type``
    classifies it (e.g. ``finding``, ``claim``, ``evidence``,
    ``report_excerpt``). ``verification_status`` records whether the content
    was verified, contradicted or is still unverified.

    Ownership is enforced by ``user_id`` (required) and optionally narrowed by
    ``project_id`` / ``session_id``. ChromaDB stores the embedding and mirrors
    these same IDs in its metadata so vector retrieval can be filtered by the
    same ownership boundaries.
    """

    __tablename__ = "research_memory"
    __table_args__ = (
        Index("ix_research_memory_user_created", "user_id", "created_at"),
        Index("ix_research_memory_project", "project_id"),
        Index("ix_research_memory_session", "session_id"),
        Index("ix_research_memory_verification", "verification_status"),
        Index("ix_research_memory_content_type", "content_type"),
        UniqueConstraint(
            "user_id",
            "project_id",
            "session_id",
            "content_hash",
            name="uq_research_memory_dedupe",
        ),
    )

    id: Mapped[uuid.UUID] = pk_uuid()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[Optional[uuid.UUID]] = fk_uuid(
        "projects", nullable=True
    )
    session_id: Mapped[Optional[uuid.UUID]] = fk_uuid(
        "research_sessions", nullable=True
    )
    source_id: Mapped[Optional[uuid.UUID]] = fk_uuid(
        "research_sources", nullable=True
    )
    # Stable hash of normalized content for duplicate detection.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    content_type: Mapped[str] = mapped_column(
        String(40), nullable=False, default="finding", index=True
    )
    content_text: Mapped[str] = mapped_column(Text, nullable=False)
    verification_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="unverified", index=True
    )
    confidence: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True, default=None
    )
    # ChromaDB document ID that stores the embedding for this memory.
    chroma_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, unique=True, index=True
    )
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    user: Mapped["User"] = relationship(back_populates="memories")
    project: Mapped[Optional["Project"]] = relationship(back_populates="memories")
    session: Mapped[Optional["ResearchSession"]] = relationship(
        back_populates="memories"
    )
    source: Mapped[Optional["ResearchSource"]] = relationship(back_populates="memories")


__all__ = ["ResearchMemory"]
