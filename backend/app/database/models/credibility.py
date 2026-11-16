"""PostgreSQL model for explainable source-credibility evaluations."""

from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import Float, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import Base, TimestampMixin, fk_uuid, pk_uuid


class SourceCredibilityEvaluation(Base, TimestampMixin):
    """One persisted heuristic evaluation for a retrieved source.

    ``source_id`` links to an existing ``research_sources`` row when one is
    available. The nullable link preserves evaluations for search results that
    have not yet been materialized as source rows.
    """

    __tablename__ = "source_credibility_evaluations"
    __table_args__ = (
        UniqueConstraint(
            "research_id",
            "source_url",
            name="uq_source_credibility_evaluation_run_url",
        ),
        Index("ix_source_credibility_evaluations_source", "source_id"),
        Index("ix_source_credibility_evaluations_research", "research_id"),
    )

    id: Mapped[uuid.UUID] = pk_uuid()
    source_id: Mapped[Optional[uuid.UUID]] = fk_uuid(
        "research_sources", nullable=True
    )
    research_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    source_url: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)
    title: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    publisher: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    source_type: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    label: Mapped[str] = mapped_column(
        String(40), nullable=False, default="insufficient_information"
    )
    factors_json: Mapped[str] = mapped_column(Text, nullable=False)
    missing_fields_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    method: Mapped[str] = mapped_column(
        String(40), nullable=False, default="heuristic_v1"
    )

    source: Mapped[Optional["ResearchSource"]] = relationship(
        back_populates="credibility_evaluations"
    )


__all__ = ["SourceCredibilityEvaluation"]
