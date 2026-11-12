"""Offline integration tests for distributed research event streaming (TASK 2.5).

The Redis Streams adapter is exercised against an in-test fake that implements
the small command surface used by the publisher/reader (``lock``, ``incr``,
``xadd``, ``xrange``, ``xread``, ``expire``, ``delete``). The worker-to-API test
uses the real SQLite execution history and the real FastAPI route, so it covers
the hand-off from a Celery-style task to an authenticated SSE subscriber.
"""

from __future__ import annotations

import dataclasses
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping, Optional

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.database.models.base import Base
from app.events import (
    BoundEventPublisher,
    EventJournal,
    NullEventStore,
    ResearchStreamEvent,
    ResearchStreamEventType,
    ResearchStreamService,
    RedisStreamPublisher,
    RedisStreamReader,
    event_to_sse,
)
from app.factory import create_app
from app.infrastructure.sqlite_database import SQLiteDatabase
from app.repositories.agent_history import AgentHistoryRepository
from app.errors import EventStreamUnavailableError
from app.orchestration.instrumentation import instrument_node
from app.security.dependencies import get_current_user
from app.services.agent_history_service import AgentHistoryService
from app.services.container import get_research_stream_service
from research_tasks import ResearchTask


# ─── Fake Redis Streams ─────────────────────────────────────────────────────


class _FakeLock:
    def __init__(self, lock: threading.Lock) -> None:
        self._lock = lock
        self._held = False

    def acquire(self, blocking: bool = True) -> bool:
        acquired = self._lock.acquire(blocking=blocking)
        self._held = acquired
        return acquired

    def release(self) -> None:
        if self._held:
            self._held = False
            self._lock.release()


class FakeRedisStreams:
    """Minimal synchronous Redis Streams client used by the adapters."""

    def __init__(self) -> None:
        self._streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self._sequences: dict[str, int] = {}
        self.expirations: dict[str, int] = {}
        # The fake lock is acquired by the publisher and then the same thread
        # calls incr/xadd, so the underlying lock must be re-entrant.
        self._lock = threading.RLock()
        self._counter = 0

    def lock(self, name: str, **_kwargs) -> _FakeLock:
        return _FakeLock(self._lock)

    def incr(self, key: str) -> int:
        with self._lock:
            self._sequences[key] = self._sequences.get(key, 0) + 1
            return self._sequences[key]

    def expire(self, key: str, ttl: int) -> bool:
        self.expirations[key] = int(ttl)
        return True

    def xadd(
        self,
        key: str,
        fields: Mapping[str, str],
        *,
        maxlen: Optional[int] = None,
        approximate: bool = True,
    ) -> str:
        with self._lock:
            self._counter += 1
            stream_id = f"{time.time_ns()}-{self._counter}"
            stream = self._streams.setdefault(key, [])
            stream.append((stream_id, dict(fields)))
            if maxlen is not None and len(stream) > maxlen:
                del stream[: len(stream) - maxlen]
            return stream_id

    def xrange(
        self,
        key: str,
        *,
        min: str = "-",
        max: str = "+",
        count: Optional[int] = None,
    ) -> list[tuple[str, dict[str, str]]]:
        with self._lock:
            entries = list(self._streams.get(key, []))
        if count is not None:
            entries = entries[:count]
        return entries

    def xread(
        self,
        streams: Mapping[str, str],
        *,
        block: Optional[int] = None,
        count: Optional[int] = None,
    ) -> Optional[list[tuple[str, list[tuple[str, dict[str, str]]]]]]:
        result: list[tuple[str, list[tuple[str, dict[str, str]]]]] = []
        with self._lock:
            for key, cursor in streams.items():
                entries = list(self._streams.get(key, []))
                if cursor and cursor != "0-0":
                    entries = [entry for entry in entries if _stream_id(entry[0]) > _stream_id(cursor)]
                if count is not None:
                    entries = entries[:count]
                if entries:
                    result.append((key, entries))
        return result or None

    def delete(self, *keys: str) -> int:
        removed = 0
        with self._lock:
            for key in keys:
                if key in self._streams:
                    del self._streams[key]
                    removed += 1
                self._sequences.pop(key, None)
        return removed


def _stream_id(value: str) -> tuple[int, int]:
    first, second = value.split("-", 1)
    return int(first), int(second)


class MemoryEventStore:
    def __init__(self) -> None:
        self.events: list[ResearchStreamEvent] = []
        self.statuses: dict[str, dict[str, Any]] = {}

    def append(self, event: ResearchStreamEvent) -> None:
        self.events.append(event)

    def list_after(
        self, research_id: str, *, after_sequence: int = 0, limit: int = 1_000
    ) -> list[ResearchStreamEvent]:
        return [
            event
            for event in self.events
            if event.research_id == research_id and event.sequence_number > after_sequence
        ][:limit]

    def set_run_status(self, research_id: str, owner_id: str, status: str) -> None:
        self.statuses[research_id] = {
            "research_id": research_id,
            "owner_id": owner_id,
            "status": status,
        }

    def get_run_status(self, research_id: str) -> Optional[dict[str, Any]]:
        return self.statuses.get(research_id)


def make_settings(tmp_path: Path, **kwargs: Any) -> Settings:
    base = Settings.from_env(env={"APP_ENV": "test"})
    return dataclasses.replace(
        base,
        reports_dir=tmp_path / "reports",
        exports_dir=tmp_path / "reports" / "exports",
        sessions_db_path=tmp_path / "sessions.db",
        event_stream_block_ms=0,
        **kwargs,
    )


def make_history(tmp_path: Path) -> AgentHistoryService:
    database = SQLiteDatabase(str(tmp_path / "sessions.db"))
    return AgentHistoryService(AgentHistoryRepository(database))


class FakeRequest:
    async def is_disconnected(self) -> bool:
        return False


# ─── Schema / transport ──────────────────────────────────────────────────────


class TestEventEnvelope:
    def test_envelope_contains_every_required_field(self) -> None:
        event = ResearchStreamEvent(
            research_id="a" * 32,
            user_id="u1",
            event_type=ResearchStreamEventType.RESEARCH_STARTED,
            payload={"topic": "x"},
            sequence_number=3,
        )
        assert event.event_id
        assert event.research_id == "a" * 32
        assert event.user_id == "u1"
        assert event.event_type.value == "research.started"
        assert event.timestamp.tzinfo is not None
        assert event.payload == {"topic": "x"}
        assert event.sequence_number == 3

    def test_all_required_event_types_are_supported(self) -> None:
        expected = {
            "research.queued",
            "research.started",
            "agent.started",
            "agent.progress",
            "agent.completed",
            "agent.failed",
            "source.discovered",
            "claim.extracted",
            "claim.verified",
            "contradiction.detected",
            "report.generated",
            "review.required",
            "research.completed",
            "research.failed",
        }
        assert {item.value for item in ResearchStreamEventType} == expected

    def test_redis_round_trip_preserves_payload_and_sequence(self) -> None:
        event = ResearchStreamEvent(
            research_id="a" * 32,
            user_id="u1",
            event_type=ResearchStreamEventType.AGENT_PROGRESS,
            payload={"progress": "searching", "count": 2},
            sequence_number=4,
        )
        restored = ResearchStreamEvent.from_redis(event.redis_fields())
        assert restored.event_id == event.event_id
        assert restored.event_type == event.event_type
        assert restored.payload == event.payload
        assert restored.sequence_number == 4


class TestRedisStreams:
    def test_publisher_orders_events_and_applies_retention(self) -> None:
        redis = FakeRedisStreams()
        publisher = RedisStreamPublisher(redis, maxlen=2, ttl_seconds=90)
        for index in range(3):
            publisher.publish(
                research_id="r1",
                user_id="u1",
                event_type=ResearchStreamEventType.AGENT_PROGRESS,
                payload={"index": index},
            )
        stream_key = publisher.stream_key("r1")
        retained = redis.xrange(stream_key)
        assert len(retained) == 2
        assert [json.loads(fields["payload"])["index"] for _, fields in retained] == [1, 2]
        assert redis.expirations[stream_key] == 90
        assert redis.expirations[publisher.sequence_key("r1")] == 90

    def test_replay_and_live_read_advance_cursor_without_duplicates(self) -> None:
        redis = FakeRedisStreams()
        publisher = RedisStreamPublisher(redis)
        reader = RedisStreamReader(redis, replay_limit=10, read_count=10, block_ms=0)
        for index in range(3):
            publisher.publish(
                research_id="r2",
                user_id="u1",
                event_type=ResearchStreamEventType.AGENT_PROGRESS,
                payload={"index": index},
            )
        replayed, cursor = reader.replay("r2", after_sequence=1)
        assert [event.payload["index"] for event in replayed] == [1, 2]
        publisher.publish(
            research_id="r2",
            user_id="u1",
            event_type=ResearchStreamEventType.RESEARCH_COMPLETED,
            payload={"status": "completed"},
        )
        live, _ = reader.read_batch(
            "r2", after_sequence=2, cursor=cursor, block_ms=0, count=10
        )
        assert [event.event_type for event in live] == [
            ResearchStreamEventType.RESEARCH_COMPLETED
        ]

    def test_cleanup_removes_stream_and_sequence(self) -> None:
        redis = FakeRedisStreams()
        publisher = RedisStreamPublisher(redis)
        reader = RedisStreamReader(redis)
        publisher.publish(
            research_id="r3",
            user_id="u1",
            event_type=ResearchStreamEventType.RESEARCH_QUEUED,
        )
        reader.cleanup("r3")
        assert redis.xrange(publisher.stream_key("r3")) == []
        assert publisher.sequence_key("r3") not in redis._sequences


class TestInstrumentation:
    def test_agent_node_publishes_started_and_completed(self) -> None:
        class RecordingPublisher:
            def __init__(self) -> None:
                self.events: list[tuple[str, dict[str, Any]]] = []

            def publish(self, event_type: str, payload: dict[str, Any]) -> None:
                self.events.append((event_type, payload))

        publisher = RecordingPublisher()
        node = instrument_node(
            "search",
            lambda state: {"status": "search_completed"},
            event_publisher=publisher,
        )
        assert node({}) == {"status": "search_completed"}
        assert [event_type for event_type, _ in publisher.events] == [
            "agent.started",
            "agent.completed",
        ]
        assert publisher.events[0][1]["agent_name"] == "search"

    def test_unconfigured_transport_fails_before_streaming(self, tmp_path: Path) -> None:
        service = ResearchStreamService(
            None,
            make_history(tmp_path),
            EventJournal(RedisStreamPublisher(FakeRedisStreams()), MemoryEventStore()),
            make_settings(tmp_path),
        )
        with pytest.raises(EventStreamUnavailableError):
            service.ensure_available()


class TestSseCompatibility:
    def test_completed_event_maps_to_legacy_complete_frame_with_id(self) -> None:
        event = ResearchStreamEvent(
            research_id="a" * 32,
            user_id="u1",
            event_type=ResearchStreamEventType.RESEARCH_COMPLETED,
            payload={
                "status": "completed",
                "provider": "groq",
                "critic_score": 9,
                "research_id": "a" * 32,
            },
            sequence_number=7,
        )
        frame = event_to_sse(event)
        assert frame.startswith("id: 7\nevent: complete\n")
        payload = json.loads(frame.split("data: ", 1)[1])
        assert payload["event"] == "complete"
        assert payload["data"]["event_type"] == "research.completed"
        assert payload["data"]["sequence_number"] == 7
        assert "user_id" not in payload["data"]

    def test_agent_started_maps_to_agent_start(self) -> None:
        event = ResearchStreamEvent(
            research_id="a" * 32,
            user_id="u1",
            event_type=ResearchStreamEventType.AGENT_STARTED,
            payload={"agent_name": "search"},
            sequence_number=1,
        )
        frame = event_to_sse(event)
        assert "event: agent_start" in frame
        assert '"agent": "search"' in frame


# ─── Service / authorization ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_service_replays_after_last_sequence_and_stops_at_terminal(tmp_path: Path) -> None:
    redis = FakeRedisStreams()
    publisher = RedisStreamPublisher(redis)
    store = MemoryEventStore()
    journal = EventJournal(publisher, store)
    history = make_history(tmp_path)
    history.register_research("a" * 32, "u1", "topic")
    for sequence_event, event_type in enumerate(
        [
            ResearchStreamEventType.RESEARCH_STARTED,
            ResearchStreamEventType.AGENT_STARTED,
            ResearchStreamEventType.RESEARCH_COMPLETED,
        ],
        start=1,
    ):
        journal.publish(
            research_id="a" * 32,
            user_id="u1",
            event_type=event_type,
            payload={"index": sequence_event},
        )
    service = ResearchStreamService(
        RedisStreamReader(redis, block_ms=0),
        history,
        journal,
        make_settings(tmp_path),
    )
    frames = [
        frame
        async for frame in service.stream(
            research_id="a" * 32,
            user_id="u1",
            last_sequence=1,
            request=FakeRequest(),
        )
    ]
    assert all("id: 1" not in frame for frame in frames)
    assert any("event: agent_start" in frame for frame in frames)
    assert any("event: complete" in frame for frame in frames)


@pytest.mark.asyncio
async def test_service_rejects_another_users_stream(tmp_path: Path) -> None:
    history = make_history(tmp_path)
    history.register_research("b" * 32, "owner", "topic")
    service = ResearchStreamService(
        RedisStreamReader(FakeRedisStreams(), block_ms=0),
        history,
        EventJournal(RedisStreamPublisher(FakeRedisStreams()), MemoryEventStore()),
        make_settings(tmp_path),
    )
    with pytest.raises(Exception) as exc:
        await service.stream(
            research_id="b" * 32,
            user_id="intruder",
            last_sequence=0,
            request=FakeRequest(),
        ).__anext__()
    assert "not found" in str(exc.value).lower()


# ─── Worker-to-API integration ──────────────────────────────────────────────


class FakeSessions:
    def save_session(self, *_args, **_kwargs) -> str:
        return "session"


class FakeReports:
    def register_from_run(self, **_kwargs) -> None:
        return None


class PublishingExecutor:
    """Fake Celery executor that emits live business events through the journal."""

    def __init__(self, journal: EventJournal) -> None:
        self.journal = journal
        self.calls: list[Any] = []

    def execute(self, request, *, user_id=None):
        self.calls.append(request)
        self.journal.publish(
            research_id=request.research_id,
            user_id=user_id or "anonymous",
            event_type=ResearchStreamEventType.AGENT_STARTED,
            payload={"agent_name": "search"},
        )
        self.journal.publish(
            research_id=request.research_id,
            user_id=user_id or "anonymous",
            event_type=ResearchStreamEventType.SOURCE_DISCOVERED,
            payload={"count": 2},
        )
        return {
            "status": "completed",
            "provider": "groq",
            "critic_score": 9,
            "revision_count": 0,
            "saved_report_path": "",
            "final_report": "",
            "search_results_raw": [{}, {}],
            "logs": [],
        }


@pytest.fixture
def worker_api(tmp_path: Path):
    settings = make_settings(tmp_path)
    database = SQLiteDatabase(str(tmp_path / "sessions.db"))
    history = AgentHistoryService(AgentHistoryRepository(database))
    redis = FakeRedisStreams()
    store = MemoryEventStore()
    journal = EventJournal(RedisStreamPublisher(redis), store)
    research_id = "c" * 32
    history.register_research(research_id, "u1", "Distributed topic")
    container = SimpleNamespace(
        settings=settings,
        agent_history=history,
        sessions=FakeSessions(),
        report_management=FakeReports(),
    )
    executor = PublishingExecutor(journal)
    task = ResearchTask(
        research_id=research_id,
        user_id="u1",
        topic="Distributed topic",
        provider="groq",
        max_revisions=2,
        approval_threshold=8,
        output_dir=str(tmp_path / "reports"),
        container=container,  # type: ignore[arg-type]
        executor=executor,  # type: ignore[arg-type]
        event_journal=journal,
    )
    yield SimpleNamespace(
        settings=settings,
        history=history,
        redis=redis,
        store=store,
        journal=journal,
        research_id=research_id,
        task=task,
        executor=executor,
    )


def test_worker_publishes_lifecycle_and_api_replays_it(worker_api) -> None:
    result = worker_api.task.run()
    assert result["status"] == "completed"
    event_types = [event.event_type for event in worker_api.store.events]
    assert ResearchStreamEventType.RESEARCH_STARTED in event_types
    assert ResearchStreamEventType.AGENT_STARTED in event_types
    assert ResearchStreamEventType.SOURCE_DISCOVERED in event_types
    assert ResearchStreamEventType.RESEARCH_COMPLETED in event_types
    assert worker_api.store.statuses[worker_api.research_id]["status"] == "completed"

    application = create_app(worker_api.settings)
    service = ResearchStreamService(
        RedisStreamReader(worker_api.redis, block_ms=0),
        worker_api.history,
        worker_api.journal,
        worker_api.settings,
    )
    application.dependency_overrides[get_current_user] = lambda: {"user_id": "u1"}
    application.dependency_overrides[get_research_stream_service] = lambda: service
    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.get(f"/research/{worker_api.research_id}/events")
    assert response.status_code == 200
    assert "event: complete" in response.text
    assert f"id: {max(event.sequence_number for event in worker_api.store.events)}" in response.text


def test_api_rejects_foreign_research_stream(worker_api) -> None:
    worker_api.task.run()
    application = create_app(worker_api.settings)
    service = ResearchStreamService(
        RedisStreamReader(worker_api.redis, block_ms=0),
        worker_api.history,
        worker_api.journal,
        worker_api.settings,
    )
    application.dependency_overrides[get_current_user] = lambda: {"user_id": "intruder"}
    application.dependency_overrides[get_research_stream_service] = lambda: service
    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.get(f"/research/{worker_api.research_id}/events")
    assert response.status_code == 404


def test_api_last_event_id_replays_only_missed_events(worker_api) -> None:
    worker_api.task.run()
    application = create_app(worker_api.settings)
    service = ResearchStreamService(
        RedisStreamReader(worker_api.redis, block_ms=0),
        worker_api.history,
        worker_api.journal,
        worker_api.settings,
    )
    application.dependency_overrides[get_current_user] = lambda: {"user_id": "u1"}
    application.dependency_overrides[get_research_stream_service] = lambda: service
    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.get(
            f"/research/{worker_api.research_id}/events",
            headers={"Last-Event-ID": "2"},
        )
    assert response.status_code == 200
    # The first two events (research.started, agent.started) are not replayed.
    assert "event: complete" in response.text
    assert "event: agent_start" not in response.text
