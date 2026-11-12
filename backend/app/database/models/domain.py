"""
app/database/models/domain.py
=============================
Core domain entities: users, projects, research sessions, plans, tasks and
agent executions.

Every table uses a UUID primary key, foreign key constraints, targeted indexes,
created/updated timestamps and ownership columns. ``owner_id`` is the Supabase
Auth ``sub`` from the validated JWT; it is never accepted from the frontend.
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


class User(Base, TimestampMixin):
    """A Supabase Auth identity that owns projects and sessions.

    No password or credentials are stored — the record merely mirrors the
    provider identity (``sub``) plus public profile attributes.
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = pk_uuid()
    # Supabase Auth `sub` — the stable, verified identity (stored as text).
    supabase_id: Mapped[str] = mapped_column(
        String(36), unique=True, nullable=False, index=True
    )
    email: Mapped[Optional[str]] = mapped_column(String(320), nullable=True, index=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    avatar_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    projects: Mapped[List["Project"]] = relationship(
        back_populates="owner", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<User id={self.id} email={self.email!r}>"


class Project(Base, TimestampMixin, SoftDeleteMixin):
    """A user-owned container for research work."""

    __tablename__ = "projects"
    __table_args__ = (Index("ix_projects_owner_created", "owner_id", "created_at"),)

    id: Mapped[uuid.UUID] = pk_uuid()
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    research_domain: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)

    owner: Mapped["User"] = relationship(back_populates="projects")
    sessions: Mapped[List["ResearchSession"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class ResearchSession(Base, TimestampMixin, SoftDeleteMixin):
    """One research run against a topic, owned by a project."""

    __tablename__ = "research_sessions"
    __table_args__ = (
        Index("ix_research_sessions_owner_created", "owner_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = pk_uuid()
    project_id: Mapped[uuid.UUID] = fk_uuid("projects")
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    priority: Mapped[str] = mapped_column(String(20), default="normal", nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    project: Mapped["Project"] = relationship(back_populates="sessions")
    plans: Mapped[List["ResearchPlan"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    executions: Mapped[List["AgentExecution"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    sources: Mapped[List["ResearchSource"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    claims: Mapped[List["ResearchClaim"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    evidence: Mapped[List["ResearchEvidence"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    contradictions: Mapped[List["ResearchContradiction"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    reports: Mapped[List["ResearchReport"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class ResearchPlan(Base, TimestampMixin):
    """A structured multi-step plan derived from a research session."""

    __tablename__ = "research_plans"

    id: Mapped[uuid.UUID] = pk_uuid()
    session_id: Mapped[uuid.UUID] = fk_uuid("research_sessions")
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    objective: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    token_budget: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    agent_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)

    session: Mapped["ResearchSession"] = relationship(back_populates="plans")
    tasks: Mapped[List["ResearchTask"]] = relationship(
        back_populates="plan", cascade="all, delete-orphan"
    )


class ResearchTask(Base, TimestampMixin):
    """A single step inside a research plan."""

    __tablename__ = "research_tasks"

    id: Mapped[uuid.UUID] = pk_uuid()
    plan_id: Mapped[uuid.UUID] = fk_uuid("research_plans")
    agent_name: Mapped[str] = mapped_column(String(40), nullable=False)
    instruction: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="queued", nullable=False)
    started_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    output_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    plan: Mapped["ResearchPlan"] = relationship(back_populates="tasks")


class AgentExecution(Base, TimestampMixin):
    """One agent run recorded against a research session."""

    __tablename__ = "agent_executions"
    __table_args__ = (
        Index("ix_agent_executions_session", "session_id", "started_at"),
    )

    id: Mapped[uuid.UUID] = pk_uuid()
    session_id: Mapped[uuid.UUID] = fk_uuid("research_sessions")
    agent_name: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), default="running", nullable=False)
    iteration: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    started_at: Mapped[datetime] = mapped_column(nullable=False)
    finished_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    latency_seconds: Mapped[Optional[float]] = mapped_column(nullable=True)
    token_usage: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    output: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    session: Mapped["ResearchSession"] = relationship(back_populates="executions")


__all__ = [
    "AgentExecution",
    "Project",
    "ResearchPlan",
    "ResearchSession",
    "ResearchTask",
    "User",
]
