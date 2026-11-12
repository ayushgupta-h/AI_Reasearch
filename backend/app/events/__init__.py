"""Distributed research event transport (Redis Streams + PostgreSQL journal)."""

from app.events.journal import EventJournal, build_event_journal, build_event_reader
from app.events.redis_streams import (
    BoundEventPublisher,
    EventPublisher,
    EventReader,
    EventTransportError,
    NullEventPublisher,
    RedisStreamPublisher,
    RedisStreamReader,
)
from app.events.schemas import (
    ResearchStreamEvent,
    ResearchStreamEventType,
    TERMINAL_EVENT_TYPES,
)
from app.events.service import ResearchStreamService
from app.events.sse import event_to_sse, terminal_event
from app.events.store import EventStore, NullEventStore, PostgresEventStore

__all__ = [
    "BoundEventPublisher",
    "EventJournal",
    "EventPublisher",
    "EventReader",
    "EventStore",
    "EventTransportError",
    "NullEventPublisher",
    "NullEventStore",
    "PostgresEventStore",
    "RedisStreamPublisher",
    "RedisStreamReader",
    "ResearchStreamEvent",
    "ResearchStreamEventType",
    "ResearchStreamService",
    "TERMINAL_EVENT_TYPES",
    "build_event_journal",
    "build_event_reader",
    "event_to_sse",
    "terminal_event",
]
