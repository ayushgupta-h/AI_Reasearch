"""
app/database/models/base.py
===========================
SQLAlchemy 2.x declarative foundations for the PostgreSQL persistence layer.

Here we define the shared ``Base``, the UUID primary key convention, and the
timestamp / soft-delete mixins used by every model. The models themselves live
in sibling modules and are re-exported from :mod:`app.database.models`.

The whole module is driver-agnostic: importing it requires only SQLAlchemy
(which is already installed), never ``psycopg``. That way existing tests and
the SQLite app paths keep working before PostgreSQL is provisioned.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column
from sqlalchemy.types import Uuid

# Consistent, well-ordered metadata for Alembic autogenerate.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base with a consistent naming convention."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def pk_uuid() -> Mapped[Any]:
    """UUID primary key column, defaulting to a fresh UUIDv4 per row."""
    return mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )


def fk_uuid(referred_table: str, *, nullable: bool = False) -> Mapped[Any]:
    """UUID foreign-key column referencing another table's primary key.

    All tables in this package use ``id`` as their primary-key column name, so
    the reference is always ``{referred_table}.id``.
    """
    return mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(f"{referred_table}.id", ondelete="CASCADE"),
        nullable=nullable,
    )


class TimestampMixin:
    """Created/updated timestamps with server-side defaults."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class SoftDeleteMixin:
    """Soft-delete support: rows are hidden, not removed.

    ``deleted_at`` is set and ``deleted`` flipped to ``True`` when a record is
    soft-deleted. Queries should filter on ``deleted.is_(False)``; the helper
    :func:`active` returns that predicate for use in ``where()`` clauses.
    """

    deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )


def active():
    """SQL predicate selecting only non-deleted rows."""
    from sqlalchemy import false

    return func.coalesce(Base.__table__.c.deleted, false()) == false()


class OwnershipMixin:
    """Every user-owned row carries an ``owner_id``.

    ``owner_id`` is the Supabase Auth ``sub`` (user UUID) from the validated
    JWT. It is always taken from the authenticated identity server-side — never
    from a client-supplied field.
    """

    @declared_attr
    def owner_id(cls) -> Mapped[Any]:
        return mapped_column(
            Uuid(as_uuid=True),
            nullable=False,
            index=True,
        )


__all__ = [
    "Base",
    "MetaData",
    "NAMING_CONVENTION",
    "OwnershipMixin",
    "SoftDeleteMixin",
    "TimestampMixin",
    "active",
    "fk_uuid",
    "pk_uuid",
]
