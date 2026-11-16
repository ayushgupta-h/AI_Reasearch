"""
app.checkpointing
=================
PostgreSQL-backed LangGraph checkpointer for durable, interruptible research
orchestration.

Provides :class:`PostgresCheckpointer` which persists LangGraph checkpoints to
PostgreSQL using the existing SQLAlchemy engine. All checkpoint queries are
scoped by ``owner_id`` (user ID) so one user's workflow can never access
another user's checkpoints.
"""

from app.checkpointing.postgres_checkpointer import PostgresCheckpointer

__all__ = ["PostgresCheckpointer"]
