"""
Durable research-event journal for PostgreSQL (TASK 2.5).

Redis Streams are the transport and replay buffer; they are not the only source
of truth. Important lifecycle events and the run's current status are also
written to PostgreSQL through the SQLAlchemy models in
``app.database.models.event``. The journal is best effort: a database outage
must not abort a research run or break the live stream.
"""

from __future__ import annotations

import json
from typing import Any, Optional, Protocol

from sqlalchemy import select

from app.config import Settings, get_settings
from app.database.models.event import ResearchEventRecord, ResearchRunStatus
from app.database.session import get_session_factory
from app.events.schemas import ResearchStreamEvent
from app.observability import get_logger

logger = get_logger("app.events.store")


class EventStore(Protocol):
    def append(self, event: ResearchStreamEvent) -> None:
        """Persist one event; may raise when the database is unavailable."""

    def list_after(
        self, research_id: str, *, after_sequence: int = 0, limit: int = 1_000
    ) -> list[ResearchStreamEvent]:
        """Read persisted events in sequence order."""

    def set_run_status(self, research_id: str, owner_id: str, status: str) -> None:
        """Upsert the current run status."""

    def get_run_status(self, research_id: str) -> Optional[dict[str, Any]]:
        """Return the persisted run status row, if present."""


class NullEventStore:
    """No-op journal used when PostgreSQL is not configured."""

    def append(self, event: ResearchStreamEvent) -> None:
        return None

    def list_after(
        self, research_id: str, *, after_sequence: int = 0, limit: int = 1_000
    ) -> list[ResearchStreamEvent]:
        return []

    def set_run_status(self, research_id: str, owner_id: str, status: str) -> None:
        return None

    def get_run_status(self, research_id: str) -> Optional[dict[str, Any]]:
        return None


class PostgresEventStore:
    """SQLAlchemy journal backed by the configured PostgreSQL database."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self._settings = settings or get_settings()
        self._available = bool(self._settings.database_url)

    @property
    def available(self) -> bool:
        return self._available

    def _factory(self):
        if not self._available:
            raise RuntimeError("PostgreSQL is not configured")
        return get_session_factory(self._settings)

    def append(self, event: ResearchStreamEvent) -> None:
        session = self._factory()()
        try:
            record = ResearchEventRecord(
                event_id=event.event_id,
                research_id=event.research_id,
                user_id=event.user_id,
                event_type=event.event_type.value,
                sequence_number=event.sequence_number,
                occurred_at=event.timestamp,
                payload=json.dumps(event.payload, ensure_ascii=False, default=str),
            )
            session.add(record)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def list_after(
        self, research_id: str, *, after_sequence: int = 0, limit: int = 1_000
    ) -> list[ResearchStreamEvent]:
        session = self._factory()()
        try:
            rows = session.scalars(
                select(ResearchEventRecord)
                .where(
                    ResearchEventRecord.research_id == research_id,
                    ResearchEventRecord.sequence_number > after_sequence,
                )
                .order_by(ResearchEventRecord.sequence_number.asc())
                .limit(max(1, min(limit, 5_000)))
            ).all()
            return [
                ResearchStreamEvent(
                    event_id=row.event_id,
                    research_id=row.research_id,
                    user_id=row.user_id,
                    event_type=row.event_type,
                    timestamp=row.occurred_at,
                    payload=json.loads(row.payload or "{}"),
                    sequence_number=row.sequence_number,
                )
                for row in rows
            ]
        finally:
            session.close()

    def set_run_status(self, research_id: str, owner_id: str, status: str) -> None:
        session = self._factory()()
        try:
            row = session.get(ResearchRunStatus, research_id)
            if row is None:
                row = ResearchRunStatus(
                    research_id=research_id,
                    owner_id=owner_id,
                    status=status,
                )
                session.add(row)
            else:
                if row.owner_id != owner_id:
                    # Never let a worker overwrite another user's run status.
                    raise PermissionError("research run owner mismatch")
                row.status = status
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_run_status(self, research_id: str) -> Optional[dict[str, Any]]:
        session = self._factory()()
        try:
            row = session.get(ResearchRunStatus, research_id)
            if row is None:
                return None
            return {
                "research_id": row.research_id,
                "owner_id": row.owner_id,
                "status": row.status,
                "updated_at": row.updated_at,
            }
        finally:
            session.close()


__all__ = ["EventStore", "NullEventStore", "PostgresEventStore"]
