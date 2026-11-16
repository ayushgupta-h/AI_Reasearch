"""
app/database/models/completeness.py
====================================
PostgreSQL models for research completeness evaluation.

Stores:
* ``research_objectives`` — decomposed sub-questions derived from the original
  research question, each with its own coverage status.
* ``research_completeness_evaluations`` — snapshot of an evaluation run,
  including gap identification and bounded additional-research recommendations.

Both tables are scoped by ``session_id`` so one user's evaluation can never
leak into another's session. Missing metadata is never fabricated — optional
columns are stored as ``NULL``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import Base, fk_uuid, pk_uuid, TimestampMixin


class ResearchObjective(Base, TimestampMixin):
    """One decomposed research objective derived from the original question.

    Each objective carries:
    * ``objective_id`` — stable, human-readable identifier (e.g. ``obj-1``).
    * ``description`` — what the objective asks.
    * ``required_evidence`` — what would satisfy the objective.
    * ``status`` — coverage status (complete / partially_complete / etc.).
    * ``related_claim_ids`` — JSON array of verified claim UUIDs that bear on
      this objective.
    * ``related_source_ids`` — JSON array of source UUIDs that bear on it.
    """

    __tablename__ = "research_objectives"
    __table_args__ = (
        Index("ix_research_objectives_session", "session_id"),
        Index("ix_research_objectives_session_status", "session_id", "status"),
    )

    id: Mapped[uuid.UUID] = pk_uuid()
    session_id: Mapped[uuid.UUID] = fk_uuid("research_sessions")
    objective_id: Mapped[str] = mapped_column(
        String(40), nullable=False, index=True
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    required_evidence: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(30), default="not_investigated", nullable=False
    )
    related_claim_ids: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    related_source_ids: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    session: Mapped["ResearchSession"] = relationship(back_populates="objectives")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ResearchObjective id={self.objective_id} status={self.status}>"


class ResearchCompletenessEvaluation(Base, TimestampMixin):
    """Snapshot of one completeness evaluation for a research session.

    Captures the evaluator's judgement at a point in time, including the
    per-objective rollup, identified gaps, and any bounded additional-research
    tasks that were recommended (and whether the budget allowed them).
    """

    __tablename__ = "research_completeness_evaluations"
    __table_args__ = (
        Index("ix_completeness_evals_session", "session_id"),
        Index("ix_completeness_evals_session_created", "session_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = pk_uuid()
    session_id: Mapped[uuid.UUID] = fk_uuid("research_sessions")
    overall_status: Mapped[str] = mapped_column(
        String(30), default="insufficient", nullable=False
    )
    completeness_score: Mapped[float] = mapped_column(
        default=0.0, nullable=False
    )
    total_objectives: Mapped[int] = mapped_column(default=0, nullable=False)
    complete_objectives: Mapped[int] = mapped_column(default=0, nullable=False)
    partial_objectives: Mapped[int] = mapped_column(default=0, nullable=False)
    insufficient_objectives: Mapped[int] = mapped_column(default=0, nullable=False)
    not_investigated_objectives: Mapped[int] = mapped_column(default=0, nullable=False)
    blocked_objectives: Mapped[int] = mapped_column(default=0, nullable=False)
    gaps_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    recommendations_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    additional_search_tasks_json: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )
    iterations_performed: Mapped[int] = mapped_column(default=0, nullable=False)
    max_iterations: Mapped[int] = mapped_column(default=0, nullable=False)

    session: Mapped["ResearchSession"] = relationship(back_populates="evaluations")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ResearchCompletenessEvaluation status={self.overall_status} score={self.completeness_score}>"


__all__ = [
    "ResearchCompletenessEvaluation",
    "ResearchObjective",
]
