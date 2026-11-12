"""
app/repositories/postgres/base.py
=================================
Generic SQLAlchemy 2.x repository base with pagination and ownership scoping.

The base enforces one invariant for every user-owned query: ``owner_id`` is a
*parameter* supplied by the authenticated identity (from the validated JWT),
never taken from a client payload. If ``owner_id`` is None for an owned
entity, the call raises rather than returning rows across users.
"""

from __future__ import annotations

from typing import Any, Generic, Optional, Sequence, Tuple, Type, TypeVar

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database.models.base import Base

ModelT = TypeVar("ModelT", bound=Base)


class PostgresRepository(Generic[ModelT]):
    """SQLAlchemy repository for a single ORM model."""

    model: Type[ModelT]
    # Name of the ownership column when the model is user-owned.
    owner_column: Optional[str] = "owner_id"
    default_sort = "created_at"
    allowed_sorts: tuple[str, ...] = ("created_at", "updated_at")

    def __init__(self, session: Session) -> None:
        self.session = session

    # ── Scope helpers ─────────────────────────────────────────────────────

    def _owner_scope(self, owner_id: Optional[str]) -> tuple[Any, ...]:
        """Return the owner predicate, or fail loudly when None for an owned table."""
        if self.owner_column is None:
            return (True,)  # type: ignore[return-value]
        if owner_id is None:
            raise ValueError(
                f"{self.model.__name__} is user-owned; owner_id is required."
            )
        column = getattr(self.model, self.owner_column)
        return (column == owner_id,)

    def _sort_order(self, sort: str, order: str) -> tuple[Any, str]:
        column_name = sort if sort in self.allowed_sorts else self.default_sort
        column = getattr(self.model, column_name)
        direction = "desc" if str(order).lower() == "desc" else "asc"
        return (column, direction)

    # ── Reads ─────────────────────────────────────────────────────────────

    def get(self, entity_id: Any, *, owner_id: Optional[str] = None) -> Optional[ModelT]:
        stmt = select(self.model).where(self.model.id == entity_id)
        stmt = stmt.where(*self._owner_scope(owner_id))
        return self.session.scalar(stmt)

    def list(
        self,
        *,
        owner_id: Optional[str] = None,
        sort: str = "created_at",
        order: str = "desc",
        limit: int = 20,
        offset: int = 0,
    ) -> Tuple[Sequence[ModelT], int]:
        """Return ``(rows, total)`` with safe pagination."""
        scope = self._owner_scope(owner_id)
        count_stmt = select(func.count()).select_from(self.model).where(*scope)
        total = int(self.session.scalar(count_stmt) or 0)

        col, direction = self._sort_order(sort, order)
        stmt = (
            select(self.model)
            .where(*scope)
            .order_by(col.desc() if direction == "desc" else col.asc())
            .offset(offset)
            .limit(limit)
        )
        rows = list(self.session.scalars(stmt).all())
        return rows, total

    # ── Writes ────────────────────────────────────────────────────────────

    def add(self, entity: ModelT) -> ModelT:
        self.session.add(entity)
        # Do not flush here; the transaction boundary owns persistence.
        return entity

    def delete(self, entity: ModelT, *, hard: bool = False) -> None:
        """Soft-delete by default; hard-delete only when explicitly requested."""
        if hard:
            self.session.delete(entity)
            return
        if hasattr(entity, "deleted") and hasattr(entity, "deleted_at"):
            from datetime import datetime, timezone

            entity.deleted = True
            entity.deleted_at = datetime.now(timezone.utc)
        else:
            self.session.delete(entity)

    def flush_and_refresh(self, entity: ModelT) -> ModelT:
        self.session.flush()
        self.session.refresh(entity)
        return entity


__all__ = ["PostgresRepository"]
