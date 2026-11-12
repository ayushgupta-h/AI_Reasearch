"""
Redis Streams transport for research progress (TASK 2.5).

Why Streams rather than Pub/Sub
-------------------------------
Redis Pub/Sub drops messages when no subscriber is connected and has no replay.
Research SSE clients reconnect, and a completed run must remain inspectable.
Every event is therefore appended to a per-research stream with ``XADD`` and
read with ``XRANGE``/``XREAD``. A TTL bounds retention and an approximate
``MAXLEN`` bounds memory under a slow consumer.

Ordering
--------
A per-research Redis lock serialises ``INCR`` and ``XADD``. Without it two
Celery workers could allocate sequences 1 and 2 but append them in the opposite
order. The lock is short-lived and only held for one Redis command pair.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional, Protocol

from app.events.schemas import ResearchStreamEvent, ResearchStreamEventType
from app.observability import get_logger

logger = get_logger("app.events.redis")


class EventTransportError(RuntimeError):
    """Redis could not publish or read the event stream."""


class EventPublisher(Protocol):
    def publish(
        self,
        *,
        research_id: str,
        user_id: str,
        event_type: ResearchStreamEventType,
        payload: Optional[Mapping[str, Any]] = None,
    ) -> ResearchStreamEvent:
        """Persist one event in the transport and return its envelope."""


class EventReader(Protocol):
    def replay(
        self, research_id: str, *, after_sequence: int = 0, limit: Optional[int] = None
    ) -> tuple[list[ResearchStreamEvent], str]:
        """Return replayed events and the cursor for the last stream entry."""

    def read_batch(
        self,
        research_id: str,
        *,
        after_sequence: int,
        cursor: str,
        block_ms: int,
        count: int,
    ) -> tuple[list[ResearchStreamEvent], str]:
        """Return new events and the updated cursor."""


class RedisStreamPublisher:
    """Writes schema-validated events to one Redis Stream per research run."""

    def __init__(
        self,
        client: Any,
        *,
        maxlen: int = 10_000,
        ttl_seconds: int = 86_400,
        key_prefix: str = "research:events",
        lock_timeout_seconds: int = 5,
    ) -> None:
        if maxlen < 1:
            raise ValueError("maxlen must be positive")
        if ttl_seconds < 1:
            raise ValueError("ttl_seconds must be positive")
        self._client = client
        self._maxlen = maxlen
        self._ttl_seconds = ttl_seconds
        self._prefix = key_prefix.rstrip(":")
        self._lock_timeout = lock_timeout_seconds

    @classmethod
    def from_url(
        cls,
        url: str,
        *,
        maxlen: int = 10_000,
        ttl_seconds: int = 86_400,
    ) -> "RedisStreamPublisher":
        import redis

        client = redis.Redis.from_url(url, decode_responses=True)
        return cls(client, maxlen=maxlen, ttl_seconds=ttl_seconds)

    def stream_key(self, research_id: str) -> str:
        return f"{self._prefix}:{research_id}"

    def sequence_key(self, research_id: str) -> str:
        return f"{self._prefix}:{research_id}:sequence"

    def lock_key(self, research_id: str) -> str:
        return f"{self._prefix}:{research_id}:publish-lock"

    def publish(
        self,
        *,
        research_id: str,
        user_id: str,
        event_type: ResearchStreamEventType,
        payload: Optional[Mapping[str, Any]] = None,
    ) -> ResearchStreamEvent:
        if not research_id:
            raise ValueError("research_id is required")
        event_type = ResearchStreamEventType(event_type)
        lock = self._client.lock(
            self.lock_key(research_id),
            timeout=self._lock_timeout,
            blocking_timeout=self._lock_timeout,
        )
        acquired = lock.acquire(blocking=True)
        if not acquired:
            raise EventTransportError("could not acquire the event publisher lock")
        try:
            sequence = int(self._client.incr(self.sequence_key(research_id)))
            event = ResearchStreamEvent(
                event_id=uuid.uuid4().hex,
                research_id=research_id,
                user_id=user_id,
                event_type=event_type,
                timestamp=datetime.now(timezone.utc),
                payload=dict(payload or {}),
                sequence_number=max(1, sequence),
            )
            self._client.xadd(
                self.stream_key(research_id),
                event.redis_fields(),
                maxlen=self._maxlen,
                approximate=True,
            )
            # Expiry is refreshed on each event; an idle or completed stream
            # disappears automatically after the retention window.
            self._client.expire(self.stream_key(research_id), self._ttl_seconds)
            self._client.expire(self.sequence_key(research_id), self._ttl_seconds)
            return event
        except Exception as exc:
            raise EventTransportError("could not publish the research event") from exc
        finally:
            try:
                lock.release()
            except Exception:  # pragma: no cover - lock may already have expired
                logger.warning("event.publish_lock_release_failed")


class RedisStreamReader:
    """Reads replay and live events from the per-research Redis Stream."""

    def __init__(
        self,
        client: Any,
        *,
        replay_limit: int = 1_000,
        read_count: int = 100,
        block_ms: int = 1_000,
        key_prefix: str = "research:events",
    ) -> None:
        if replay_limit < 1 or read_count < 1:
            raise ValueError("replay_limit and read_count must be positive")
        if block_ms < 0:
            raise ValueError("block_ms cannot be negative")
        self._client = client
        self._replay_limit = replay_limit
        self._read_count = read_count
        self._block_ms = block_ms
        self._prefix = key_prefix.rstrip(":")

    @classmethod
    def from_url(
        cls,
        url: str,
        *,
        replay_limit: int = 1_000,
        read_count: int = 100,
        block_ms: int = 1_000,
    ) -> "RedisStreamReader":
        import redis

        client = redis.Redis.from_url(url, decode_responses=True)
        return cls(
            client,
            replay_limit=replay_limit,
            read_count=read_count,
            block_ms=block_ms,
        )

    def stream_key(self, research_id: str) -> str:
        return f"{self._prefix}:{research_id}"

    def _parse_entries(self, entries: Iterable[tuple[Any, Mapping[str, Any]]]) -> list[ResearchStreamEvent]:
        parsed: list[ResearchStreamEvent] = []
        for _entry_id, fields in entries:
            try:
                parsed.append(ResearchStreamEvent.from_redis(fields))
            except Exception:
                logger.warning("event.parse_failed", extra={"error_code": "event_stream_corrupt"})
        return parsed

    def replay(
        self,
        research_id: str,
        *,
        after_sequence: int = 0,
        limit: Optional[int] = None,
    ) -> tuple[list[ResearchStreamEvent], str]:
        try:
            raw = self._client.xrange(
                self.stream_key(research_id),
                min="-",
                max="+",
                count=limit or self._replay_limit,
            )
        except Exception as exc:
            raise EventTransportError("could not replay the research event stream") from exc
        events = [
            event
            for event in self._parse_entries(raw or [])
            if event.sequence_number > after_sequence
        ]
        cursor = raw[-1][0] if raw else "0-0"
        return events, str(cursor)

    def read_batch(
        self,
        research_id: str,
        *,
        after_sequence: int,
        cursor: str,
        block_ms: Optional[int] = None,
        count: Optional[int] = None,
    ) -> tuple[list[ResearchStreamEvent], str]:
        block = self._block_ms if block_ms is None else block_ms
        # redis-py treats block=0 as "block forever"; 0 here means non-blocking.
        redis_block = None if block == 0 else block
        limit = count or self._read_count
        try:
            response = self._client.xread(
                {self.stream_key(research_id): cursor or "0-0"},
                block=redis_block,
                count=limit,
            )
        except Exception as exc:
            raise EventTransportError("could not read the research event stream") from exc

        entries: list[tuple[Any, Mapping[str, Any]]] = []
        if response:
            # redis-py returns [("stream-key", [(id, fields), ...])].
            if isinstance(response, Mapping):
                entries = list(response.get(self.stream_key(research_id), []))
            else:
                for stream_name, stream_entries in response:
                    name = stream_name.decode() if isinstance(stream_name, bytes) else str(stream_name)
                    if name == self.stream_key(research_id):
                        entries = list(stream_entries)
                        break
        events = [
            event
            for event in self._parse_entries(entries)
            if event.sequence_number > after_sequence
        ]
        if entries:
            cursor = str(entries[-1][0])
        return events, cursor

    def cleanup(self, research_id: str) -> None:
        """Remove a run's stream and sequence key (manual retention override)."""
        try:
            self._client.delete(self.stream_key(research_id), f"{self.stream_key(research_id)}:sequence")
        except Exception as exc:
            raise EventTransportError("could not clean up the research event stream") from exc


class NullEventPublisher:
    """No-op publisher for local tests and workers without Redis configured."""

    def publish(
        self,
        *,
        research_id: str,
        user_id: str,
        event_type: ResearchStreamEventType,
        payload: Optional[Mapping[str, Any]] = None,
    ) -> ResearchStreamEvent:
        return ResearchStreamEvent(
            research_id=research_id,
            user_id=user_id,
            event_type=event_type,
            timestamp=datetime.now(timezone.utc),
            payload=dict(payload or {}),
            sequence_number=0,
        )


class BoundEventPublisher:
    """A publisher bound to one run, used by the orchestrator and Celery task."""

    def __init__(self, publisher: EventPublisher, *, research_id: str, user_id: str) -> None:
        self._publisher = publisher
        self.research_id = research_id
        self.user_id = user_id or "anonymous"

    def publish(
        self,
        event_type: ResearchStreamEventType,
        payload: Optional[Mapping[str, Any]] = None,
    ) -> ResearchStreamEvent:
        return self._publisher.publish(
            research_id=self.research_id,
            user_id=self.user_id,
            event_type=event_type,
            payload=payload,
        )


__all__ = [
    "BoundEventPublisher",
    "EventPublisher",
    "EventReader",
    "EventTransportError",
    "NullEventPublisher",
    "RedisStreamPublisher",
    "RedisStreamReader",
]
