"""
app/database/models/research.py
===============================
Research knowledge entities: sources, claims, evidence, their association
table, and recorded contradictions.

``ClaimEvidence`` is an *association object*: it carries extra attributes
(stance and weight) beyond the two foreign keys, so it is a proper table, not a
bare many-to-many join.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import (
    Base,
    TimestampMixin,
    fk_uuid,
    pk_uuid,
)


class ResearchSource(Base, TimestampMixin):
    """A retrieved document/URL used as evidence in a research session."""

    __tablename__ = "research_sources"
    __table_args__ = (Index("ix_research_sources_session", "session_id"),)

    id: Mapped[uuid.UUID] = pk_uuid()
    session_id: Mapped[uuid.UUID] = fk_uuid("research_sessions")
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    url: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)
    source_type: Mapped[str] = mapped_column(String(30), default="web", nullable=False)
    snippet: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    author: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    published_date: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    accessed_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    citation_status: Mapped[str] = mapped_column(
        String(20), default="unverified", nullable=False
    )
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    session: Mapped["ResearchSession"] = relationship(back_populates="sources")
    evidence: Mapped[List["ResearchEvidence"]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )


class ResearchClaim(Base, TimestampMixin):
    """A factual assertion extracted from the research material."""

    __tablename__ = "research_claims"
    __table_args__ = (Index("ix_research_claims_session", "session_id"),)

    id: Mapped[uuid.UUID] = pk_uuid()
    session_id: Mapped[uuid.UUID] = fk_uuid("research_sessions")
    text: Mapped[str] = mapped_column(Text, nullable=False)
    claim_type: Mapped[str] = mapped_column(
        String(30), default="factual", nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(30), default="unverified", nullable=False
    )
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    session: Mapped["ResearchSession"] = relationship(back_populates="claims")
    evidence_links: Mapped[List["ClaimEvidence"]] = relationship(
        back_populates="claim", cascade="all, delete-orphan"
    )
    contradictions_as_a: Mapped[List["ResearchContradiction"]] = relationship(
        foreign_keys="ResearchContradiction.claim_a_id",
        back_populates="claim_a",
        cascade="all, delete-orphan",
    )
    contradictions_as_b: Mapped[List["ResearchContradiction"]] = relationship(
        foreign_keys="ResearchContradiction.claim_b_id",
        back_populates="claim_b",
        cascade="all, delete-orphan",
    )


class ResearchEvidence(Base, TimestampMixin):
    """A snippet of evidence that supports or contradicts one or more claims."""

    __tablename__ = "research_evidence"
    __table_args__ = (Index("ix_research_evidence_session", "session_id"),)

    id: Mapped[uuid.UUID] = pk_uuid()
    session_id: Mapped[uuid.UUID] = fk_uuid("research_sessions")
    source_id: Mapped[Optional[uuid.UUID]] = fk_uuid("research_sources", nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    stance: Mapped[str] = mapped_column(String(20), default="neutral", nullable=False)
    weight: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    verified: Mapped[bool] = mapped_column(default=False, nullable=False)
    location: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    session: Mapped["ResearchSession"] = relationship(back_populates="evidence")
    source: Mapped[Optional["ResearchSource"]] = relationship(
        back_populates="evidence"
    )
    claim_links: Mapped[List["ClaimEvidence"]] = relationship(
        back_populates="evidence", cascade="all, delete-orphan"
    )


class ClaimEvidence(Base):
    """Association object linking claims to evidence with a stance/weight."""

    __tablename__ = "claim_evidence"
    __table_args__ = (
        UniqueConstraint("claim_id", "evidence_id", name="uq_claim_evidence"),
        Index("ix_claim_evidence_evidence", "evidence_id"),
    )

    claim_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_claims.id", ondelete="CASCADE"),
        primary_key=True,
    )
    evidence_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_evidence.id", ondelete="CASCADE"),
        primary_key=True,
    )
    stance: Mapped[str] = mapped_column(String(20), default="supports", nullable=False)
    weight: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False)

    claim: Mapped["ResearchClaim"] = relationship(back_populates="evidence_links")
    evidence: Mapped["ResearchEvidence"] = relationship(back_populates="claim_links")


class ResearchContradiction(Base, TimestampMixin):
    """A recorded contradiction between two claims in a session."""

    __tablename__ = "research_contradictions"
    __table_args__ = (Index("ix_research_contradictions_session", "session_id"),)

    id: Mapped[uuid.UUID] = pk_uuid()
    session_id: Mapped[uuid.UUID] = fk_uuid("research_sessions")
    claim_a_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_claims.id", ondelete="CASCADE"), nullable=False
    )
    claim_b_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_claims.id", ondelete="CASCADE"), nullable=False
    )
    severity: Mapped[str] = mapped_column(String(20), default="medium", nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), default="unresolved", nullable=False
    )
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resolution_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    session: Mapped["ResearchSession"] = relationship(back_populates="contradictions")
    claim_a: Mapped["ResearchClaim"] = relationship(
        foreign_keys=[claim_a_id], back_populates="contradictions_as_a"
    )
    claim_b: Mapped["ResearchClaim"] = relationship(
        foreign_keys=[claim_b_id], back_populates="contradictions_as_b"
    )


__all__ = [
    "ClaimEvidence",
    "ResearchClaim",
    "ResearchContradiction",
    "ResearchEvidence",
    "ResearchSource",
]
