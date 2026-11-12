"""
app/database/models/report.py
=============================
Report artifacts: reports, their versions, and the reviewer workflow
(request + comments).

A report belongs to a session; each revision creates a new ``ReportVersion``.
A ``ReviewRequest`` links a reviewer to a report; the reviewer's decision
(approve / request changes) lives on the request, with individual comments in
``ReviewComment``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import (
    Base,
    SoftDeleteMixin,
    TimestampMixin,
    fk_uuid,
    pk_uuid,
)


class ResearchReport(Base, TimestampMixin, SoftDeleteMixin):
    """The compiled markdown/document artifact for a research session."""

    __tablename__ = "research_reports"
    __table_args__ = (Index("ix_research_reports_session", "session_id"),)

    id: Mapped[uuid.UUID] = pk_uuid()
    session_id: Mapped[uuid.UUID] = fk_uuid("research_sessions")
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    format: Mapped[str] = mapped_column(String(10), default="md", nullable=False)
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    exporter_version: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    artifact_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    session: Mapped["ResearchSession"] = relationship(back_populates="reports")
    versions: Mapped[List["ReportVersion"]] = relationship(
        back_populates="report", cascade="all, delete-orphan"
    )
    review_requests: Mapped[List["ReviewRequest"]] = relationship(
        back_populates="report", cascade="all, delete-orphan"
    )


class ReportVersion(Base, TimestampMixin):
    """One immutable iteration of a report."""

    __tablename__ = "report_versions"
    __table_args__ = (
        Index("ix_report_versions_report", "report_id", "version_number"),
    )

    id: Mapped[uuid.UUID] = pk_uuid()
    report_id: Mapped[uuid.UUID] = fk_uuid("research_reports")
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="draft", nullable=False)
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    published_by: Mapped[Optional[uuid.UUID]] = mapped_column(nullable=True)

    report: Mapped["ResearchReport"] = relationship(back_populates="versions")


class ReviewRequest(Base, TimestampMixin):
    """A reviewer's request to approve or request changes to a report."""

    __tablename__ = "review_requests"
    __table_args__ = (
        Index("ix_review_requests_report", "report_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = pk_uuid()
    report_id: Mapped[uuid.UUID] = fk_uuid("research_reports")
    reviewer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(20), default="pending", nullable=False
    )
    decision: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    decided_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    reviewer_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    report: Mapped["ResearchReport"] = relationship(back_populates="review_requests")
    comments: Mapped[List["ReviewComment"]] = relationship(
        back_populates="review_request", cascade="all, delete-orphan"
    )


class ReviewComment(Base, TimestampMixin):
    """A single thread of reviewer feedback on a report."""

    __tablename__ = "review_comments"
    __table_args__ = (Index("ix_review_comments_request", "review_request_id"),)

    id: Mapped[uuid.UUID] = pk_uuid()
    review_request_id: Mapped[uuid.UUID] = fk_uuid("review_requests")
    author_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    is_resolved: Mapped[bool] = mapped_column(default=False, nullable=False)

    review_request: Mapped["ReviewRequest"] = relationship(back_populates="comments")


__all__ = [
    "ReportVersion",
    "ResearchReport",
    "ReviewComment",
    "ReviewRequest",
]
