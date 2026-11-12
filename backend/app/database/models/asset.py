"""
app/database/models/asset.py
============================
Operational entities: uploaded documents, audit log entries and model usage
records.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models.base import (
    Base,
    TimestampMixin,
    fk_uuid,
    pk_uuid,
)


class UploadedDocument(Base, TimestampMixin):
    """A file uploaded by a user and attached to a project."""

    __tablename__ = "uploaded_documents"
    __table_args__ = (Index("ix_uploaded_documents_owner", "owner_id"),)

    id: Mapped[uuid.UUID] = pk_uuid()
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[Optional[uuid.UUID]] = fk_uuid("projects", nullable=True)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(120), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    purpose: Mapped[str] = mapped_column(String(30), default="source_document", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="uploaded", nullable=False)
    chunk_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class AuditLog(Base):
    """Append-only record of security-relevant actions."""

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_actor_time", "actor_id", "occurred_at"),
        Index("ix_audit_logs_action_time", "action", "occurred_at"),
    )

    id: Mapped[uuid.UUID] = pk_uuid()
    occurred_at: Mapped[datetime] = mapped_column(nullable=False)
    actor_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    resource_type: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    resource_id: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    outcome: Mapped[str] = mapped_column(String(20), default="success", nullable=False)
    ip_address: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class ModelUsage(Base):
    """Aggregatable LLM usage record for cost/quotas."""

    __tablename__ = "model_usage"
    __table_args__ = (Index("ix_model_usage_user_time", "user_id", "occurred_at"),)

    id: Mapped[uuid.UUID] = pk_uuid()
    occurred_at: Mapped[datetime] = mapped_column(nullable=False, index=True)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    session_id: Mapped[Optional[uuid.UUID]] = fk_uuid("research_sessions", nullable=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Numeric(16, 8), default=0, nullable=False)

__all__ = ["AuditLog", "ModelUsage", "UploadedDocument"]
