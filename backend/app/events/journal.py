"""Event journal that combines Redis transport with PostgreSQL durability."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from app.config import Settings, get_settings
from app.events.redis_streams import (
    EventPublisher,
    NullEventPublisher,
    RedisStreamPublisher,
    RedisStreamReader,
)
from app.events.schemas import ResearchStreamEvent, ResearchStreamEventType
from app.events.store import EventStore, NullEventStore, PostgresEventStore
from app.observability import get_logger

logger = get_logger("app.events.journal")


class EventJournal:
    """Publishes to Redis and mirrors the event + run status to PostgreSQL.

    Transport and persistence failures are isolated: a Redis outage still
    leaves the business event and status in PostgreSQL, and a PostgreSQL outage
    does not stop live progress from reaching the browser.
    """

    def __init__(self, publisher: EventPublisher, store: EventStore) -> None:
        self._publisher = publisher
        self._store = store

    @property
    def publisher(self) -> EventPublisher:
        return self._publisher

    def publish(
        self,
        *,
        research_id: str,
        user_id: str,
        event_type: ResearchStreamEventType,
        payload: Optional[Mapping[str, Any]] = None,
    ) -> ResearchStreamEvent:
        payload_dict = dict(payload or {})
        event_type = ResearchStreamEventType(event_type)
        try:
            event = self._publisher.publish(
                research_id=research_id,
                user_id=user_id,
                event_type=event_type,
                payload=payload_dict,
            )
        except Exception:
            logger.exception(
                "event.redis_publish_failed",
                extra={"research_id": research_id, "event_type": event_type.value},
            )
            # Keep an auditable business event even when Redis is unavailable.
            event = ResearchStreamEvent(
                research_id=research_id,
                user_id=user_id,
                event_type=event_type,
                timestamp=datetime.now(timezone.utc),
                payload=payload_dict,
                sequence_number=0,
            )
        try:
            self._store.append(event)
        except Exception:
            logger.exception(
                "event.postgres_persist_failed",
                extra={"research_id": research_id, "event_type": event_type.value},
            )
        return event

    def set_run_status(self, research_id: str, owner_id: str, status: str) -> None:
        try:
            self._store.set_run_status(research_id, owner_id, status)
        except Exception:
            logger.exception(
                "event.postgres_status_failed",
                extra={"research_id": research_id, "status": status},
            )

    def list_persisted(
        self, research_id: str, *, after_sequence: int = 0, limit: int = 1_000
    ) -> list[ResearchStreamEvent]:
        try:
            return self._store.list_after(
                research_id, after_sequence=after_sequence, limit=limit
            )
        except Exception:
            logger.exception("event.postgres_replay_failed", extra={"research_id": research_id})
            return []

    def get_run_status(self, research_id: str) -> Optional[dict[str, Any]]:
        try:
            return self._store.get_run_status(research_id)
        except Exception:
            logger.exception("event.postgres_status_read_failed", extra={"research_id": research_id})
            return None


def _redis_url(settings: Settings) -> str:
    return (settings.redis_url or settings.celery_broker_url).strip()


def build_event_journal(settings: Optional[Settings] = None) -> EventJournal:
    """Build a journal from settings without opening a Redis connection."""
    cfg = settings or get_settings()
    url = _redis_url(cfg)
    if url:
        publisher: EventPublisher = RedisStreamPublisher.from_url(
            url,
            maxlen=cfg.event_stream_maxlen,
            ttl_seconds=cfg.event_stream_ttl_seconds,
        )
    else:
        publisher = NullEventPublisher()

    store: EventStore
    if cfg.database_url:
        store = PostgresEventStore(cfg)
    else:
        store = NullEventStore()
    return EventJournal(publisher, store)


def build_event_reader(settings: Optional[Settings] = None) -> Optional[RedisStreamReader]:
    """Return a reader when Redis is configured; ``None`` otherwise."""
    cfg = settings or get_settings()
    url = _redis_url(cfg)
    if not url:
        return None
    return RedisStreamReader.from_url(
        url,
        replay_limit=cfg.event_stream_replay_limit,
        read_count=cfg.event_stream_read_count,
        block_ms=cfg.event_stream_block_ms,
    )


__all__ = ["EventJournal", "build_event_journal", "build_event_reader"]
