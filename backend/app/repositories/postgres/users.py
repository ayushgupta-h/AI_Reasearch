"""
app/repositories/postgres/users.py
==================================
User repository keyed by Supabase Auth identity.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models.domain import User
from app.repositories.postgres.base import PostgresRepository


class UserRepository(PostgresRepository[User]):
    model = User
    owner_column = None  # users have no own column; they are the owner
    default_sort = "created_at"
    allowed_sorts = ("created_at", "updated_at")

    def __init__(self, session: Session) -> None:
        super().__init__(session)

    def by_supabase_id(self, supabase_id: str) -> Optional[User]:
        return self.session.scalar(
            select(User).where(User.supabase_id == supabase_id)
        )

    def get_or_create(self, *, supabase_id: str, email: Optional[str] = None) -> User:
        existing = self.by_supabase_id(supabase_id)
        if existing is not None:
            return existing
        user = User(supabase_id=supabase_id, email=email)
        self.session.add(user)
        self.session.flush()
        return user


__all__ = ["UserRepository"]
