"""
tests/test_infrastructure_ports.py
==================================
Ports, local adapters, container wiring and research-executor delegation.

Everything here is single-process and offline; no managed service is contacted
and no production adapter is exercised or simulated.
"""

from __future__ import annotations

import dataclasses
import io
import queue
import sys
import threading
from pathlib import Path

import pytest

from app.config import get_settings
from app.errors import DatabaseError, OrchestrationError
from app.infrastructure import (
    CacheService,
    Database,
    EventBus,
    EventPublisher,
    EventSubscriber,
    InProcessEventBus,
    InvalidSignedUrl,
    JobQueue,
    JobStatus,
    LocalCacheService,
    LocalJobQueue,
    LocalObjectStorage,
    LocalResearchExecutor,
    ObjectStorage,
    ResearchExecutionRequest,
    ResearchExecutor,
    SQLiteDatabase,
)
from app.schemas.requests import ResearchRunRequest
from app.services.container import ServiceContainer
from app.services.research_service import ResearchService


class _Clock:
    """Deterministic clock for TTL and signed-URL expiry tests."""

    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# ─── Contract conformance ────────────────────────────────────────────────────


class TestPortConformance:
    def test_local_adapters_satisfy_their_protocols(self, tmp_path: Path) -> None:
        bus = InProcessEventBus()
        assert isinstance(LocalCacheService(), CacheService)
        assert isinstance(bus, EventPublisher)
        assert isinstance(bus, EventSubscriber)
        assert isinstance(bus, EventBus)
        assert isinstance(LocalObjectStorage(tmp_path / "objects"), ObjectStorage)
        assert isinstance(SQLiteDatabase(tmp_path / "app.db"), Database)
        assert isinstance(LocalResearchExecutor(), ResearchExecutor)

        jobs = LocalJobQueue()
        try:
            assert isinstance(jobs, JobQueue)
        finally:
            jobs.close()

    def test_no_managed_service_clients_are_imported(self) -> None:
        """Local adapters must not eagerly pull heavy managed-service clients.

        NOTE: ``celery`` and ``redis`` are deliberately excluded from this guard
        because they are first-party dependencies of TASK 2.2 (their modules are
        imported by the task layer, not by the local adapter package). We still
        guard the clients that are NOT part of the app's dependency set.
        """
        import app.infrastructure  # noqa: F401 - ensure the package is loaded

        for module in ("psycopg2", "minio"):
            assert module not in sys.modules


# ─── Cache ───────────────────────────────────────────────────────────────────


class TestLocalCache:
    def test_set_get_and_missing_key(self) -> None:
        cache = LocalCacheService()
        assert cache.get("missing") is None
        cache.set("topic", "AI in medicine")
        assert cache.get("topic") == "AI in medicine"
        assert cache.exists("topic") is True

    def test_ttl_expiry_uses_the_injected_clock(self) -> None:
        clock = _Clock()
        cache = LocalCacheService(clock=clock)
        cache.set("token", "abc", ttl_seconds=30)
        clock.advance(29)
        assert cache.get("token") == "abc"
        clock.advance(1)
        assert cache.get("token") is None
        assert cache.exists("token") is False

    def test_delete_reports_whether_the_key_existed(self) -> None:
        cache = LocalCacheService()
        cache.set("k", "v")
        assert cache.delete("k") is True
        assert cache.delete("k") is False

    def test_clear_and_length(self) -> None:
        cache = LocalCacheService()
        cache.set("a", "1")
        cache.set("b", "2")
        assert len(cache) == 2
        cache.clear()
        assert len(cache) == 0

    def test_max_entries_evicts_the_oldest(self) -> None:
        cache = LocalCacheService(max_entries=2)
        cache.set("a", "1")
        cache.set("b", "2")
        cache.set("c", "3")
        assert cache.get("a") is None
        assert cache.get("b") == "2"
        assert cache.get("c") == "3"

    def test_invalid_values_and_ttls_are_rejected(self) -> None:
        cache = LocalCacheService()
        with pytest.raises(ValueError):
            cache.set("", "value")
        with pytest.raises(TypeError):
            cache.set("k", 123)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            cache.set("k", "v", ttl_seconds=0)


# ─── Job queue ───────────────────────────────────────────────────────────────


class TestLocalJobQueue:
    def test_successful_job_records_result_and_timestamps(self) -> None:
        jobs = LocalJobQueue(max_workers=2)
        try:
            jobs.register("double", lambda ctx, payload: payload["n"] * 2)
            job_id = jobs.enqueue("double", {"n": 21})

            record = jobs.wait(job_id, timeout=3)
            assert record.status is JobStatus.SUCCEEDED
            assert record.result == 42
            assert record.started_at is not None
            assert record.finished_at is not None
            assert record.error is None
        finally:
            jobs.close()

    def test_failed_handler_records_the_error(self) -> None:
        jobs = LocalJobQueue(max_workers=1)

        def explode(ctx, payload):
            raise ValueError("pipeline exploded")

        try:
            jobs.register("boom", explode)
            record = jobs.wait(jobs.enqueue("boom"), timeout=3)
            assert record.status is JobStatus.FAILED
            assert "ValueError: pipeline exploded" in (record.error or "")
        finally:
            jobs.close()

    def test_unknown_handler_and_job_raise_key_error(self) -> None:
        jobs = LocalJobQueue()
        try:
            with pytest.raises(KeyError):
                jobs.enqueue("missing")
            with pytest.raises(KeyError):
                jobs.get_status("unknown")
            with pytest.raises(KeyError):
                jobs.cancel("unknown")
        finally:
            jobs.close()

    def test_duplicate_handler_registration_is_rejected(self) -> None:
        jobs = LocalJobQueue()
        try:
            jobs.register("once", lambda ctx, payload: None)
            with pytest.raises(ValueError):
                jobs.register("once", lambda ctx, payload: None)
        finally:
            jobs.close()

    def test_queued_job_can_be_cancelled_before_start(self) -> None:
        jobs = LocalJobQueue(max_workers=1)
        started = threading.Event()
        release = threading.Event()

        def blocker(ctx, payload):
            started.set()
            release.wait(5)
            return "blocked"

        try:
            jobs.register("block", blocker)
            jobs.register("quick", lambda ctx, payload: "quick")

            first = jobs.enqueue("block")
            assert started.wait(3)
            second = jobs.enqueue("quick")

            assert jobs.cancel(second) is True
            assert jobs.wait(second, timeout=3).status is JobStatus.CANCELLED

            release.set()
            assert jobs.wait(first, timeout=5).status is JobStatus.SUCCEEDED
        finally:
            release.set()
            jobs.close()

    def test_running_job_cancellation_is_cooperative(self) -> None:
        jobs = LocalJobQueue(max_workers=1)
        started = threading.Event()

        def cooperative(ctx, payload):
            started.set()
            ctx.cancel_event.wait(5)
            return "stopped"

        try:
            jobs.register("cooperative", cooperative)
            job_id = jobs.enqueue("cooperative")
            assert started.wait(3)

            # Already running: the caller is told it must wait for the handler.
            assert jobs.cancel(job_id) is False
            record = jobs.wait(job_id, timeout=5)
            assert record.status is JobStatus.CANCELLED
            assert record.cancel_requested is True
        finally:
            jobs.close()

    def test_closed_queue_rejects_new_jobs(self) -> None:
        jobs = LocalJobQueue()
        jobs.register("noop", lambda ctx, payload: None)
        jobs.close()
        with pytest.raises(RuntimeError):
            jobs.enqueue("noop")
        jobs.close()  # idempotent


# ─── Object storage ─────────────────────────────────────────────────────────


@pytest.fixture
def storage(tmp_path: Path) -> LocalObjectStorage:
    return LocalObjectStorage(
        tmp_path / "objects", signing_key=b"test-signing-key", clock=_Clock()
    )


class TestLocalObjectStorage:
    def test_upload_download_roundtrip_with_metadata(
        self, storage: LocalObjectStorage
    ) -> None:
        info = storage.upload(
            "reports/summary.md",
            b"# Findings",
            content_type="text/markdown",
            metadata={"topic": "AI"},
        )
        assert info.key == "reports/summary.md"
        assert info.size == 10
        assert storage.exists("reports/summary.md") is True
        assert storage.download("reports/summary.md") == b"# Findings"

        stat = storage.stat("reports/summary.md")
        assert stat.content_type == "text/markdown"
        assert stat.metadata == {"topic": "AI"}

    def test_binary_file_objects_are_accepted(self, storage: LocalObjectStorage) -> None:
        info = storage.upload("blob.bin", io.BytesIO(b"\x00\x01\x02"))
        assert info.size == 3
        assert storage.download("blob.bin") == b"\x00\x01\x02"

    def test_missing_objects_raise_file_not_found(
        self, storage: LocalObjectStorage
    ) -> None:
        with pytest.raises(FileNotFoundError):
            storage.download("nope.txt")
        with pytest.raises(FileNotFoundError):
            storage.stat("nope.txt")

    def test_delete_reports_whether_the_object_existed(
        self, storage: LocalObjectStorage
    ) -> None:
        storage.upload("temp.txt", b"x")
        assert storage.delete("temp.txt") is True
        assert storage.delete("temp.txt") is False
        assert storage.exists("temp.txt") is False

    @pytest.mark.parametrize(
        "key",
        ["../escape.txt", "/absolute.txt", "C:/windows.txt", "a/../../b.txt", ""],
    )
    def test_unsafe_keys_are_rejected(
        self, storage: LocalObjectStorage, key: str
    ) -> None:
        with pytest.raises(ValueError):
            storage.upload(key, b"x")

    def test_signed_url_roundtrip(self, storage: LocalObjectStorage) -> None:
        storage.upload("report.pdf", b"%PDF")
        signed = storage.generate_signed_url("report.pdf", expires_in=60)

        assert signed.key == "report.pdf"
        assert signed.url.startswith("local://")
        assert storage.verify_signed_url(signed.url) == "report.pdf"

    def test_expired_signed_url_is_rejected(self, tmp_path: Path) -> None:
        clock = _Clock()
        storage = LocalObjectStorage(
            tmp_path / "objects", signing_key=b"k" * 32, clock=clock
        )
        storage.upload("report.pdf", b"%PDF")
        signed = storage.generate_signed_url("report.pdf", expires_in=10)

        clock.advance(11)
        with pytest.raises(InvalidSignedUrl):
            storage.verify_signed_url(signed.url)

    def test_tampered_signed_urls_are_rejected(self, storage: LocalObjectStorage) -> None:
        storage.upload("report.pdf", b"%PDF")
        storage.upload("other.pdf", b"%PDF")
        signed = storage.generate_signed_url("report.pdf", expires_in=60)

        with pytest.raises(InvalidSignedUrl):
            storage.verify_signed_url(signed.url.replace("report.pdf", "other.pdf"))
        with pytest.raises(InvalidSignedUrl):
            storage.verify_signed_url(signed.url + "0")
        with pytest.raises(InvalidSignedUrl):
            storage.verify_signed_url(signed.url, method="DELETE")

    def test_non_local_scheme_is_rejected(self, storage: LocalObjectStorage) -> None:
        with pytest.raises(InvalidSignedUrl):
            storage.verify_signed_url(
                "https://example.com/report.pdf?method=GET&expires=1&signature=x"
            )


# ─── Events ─────────────────────────────────────────────────────────────────


class TestInProcessEventBus:
    def test_publish_delivers_payload_and_returns_envelope(self) -> None:
        bus = InProcessEventBus()
        received = []
        bus.subscribe("research.completed", received.append)

        event = bus.publish("research.completed", {"session_id": "s-1"})

        assert event.name == "research.completed"
        assert event.payload == {"session_id": "s-1"}
        assert len(event.event_id) == 32
        assert [item.event_id for item in received] == [event.event_id]

    def test_wildcard_subscriber_sees_every_event(self) -> None:
        bus = InProcessEventBus()
        names: list[str] = []
        bus.subscribe("*", lambda event: names.append(event.name))

        bus.publish("a")
        bus.publish("b", {"x": 1})

        assert names == ["a", "b"]

    def test_unsubscribe_stops_delivery(self) -> None:
        bus = InProcessEventBus()
        received = []
        unsubscribe = bus.subscribe("topic", received.append)

        bus.publish("topic")
        unsubscribe()
        bus.publish("topic")

        assert len(received) == 1
        assert bus.subscriber_count("topic") == 0

    def test_one_failing_handler_does_not_block_others(self) -> None:
        bus = InProcessEventBus()
        received = []

        def broken(event):
            raise RuntimeError("subscriber boom")

        bus.subscribe("topic", broken)
        bus.subscribe("topic", received.append)

        bus.publish("topic")
        assert len(received) == 1

    def test_invalid_subscriptions_are_rejected(self) -> None:
        bus = InProcessEventBus()
        with pytest.raises(ValueError):
            bus.publish("")
        with pytest.raises(TypeError):
            bus.subscribe("topic", "not callable")  # type: ignore[arg-type]


# ─── Database ───────────────────────────────────────────────────────────────


class TestSQLiteDatabase:
    @pytest.fixture
    def db(self, tmp_path: Path) -> SQLiteDatabase:
        database = SQLiteDatabase(tmp_path / "app.db")
        database.execute(
            "CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT NOT NULL)"
        )
        return database

    def test_statement_and_query_helpers(self, db: SQLiteDatabase) -> None:
        assert db.execute("INSERT INTO notes (body) VALUES (?)", ("first",)) == 1
        assert db.fetch_one("SELECT body FROM notes") == {"body": "first"}
        assert db.fetch_all("SELECT body FROM notes") == [{"body": "first"}]
        assert db.fetch_one("SELECT body FROM notes WHERE id = ?", (999,)) is None

    def test_executemany_inserts_every_row(self, db: SQLiteDatabase) -> None:
        assert db.executemany("INSERT INTO notes (body) VALUES (?)", [("a",), ("b",)]) == 2
        assert len(db.fetch_all("SELECT id FROM notes")) == 2

    def test_transaction_commits_on_success(self, db: SQLiteDatabase) -> None:
        with db.transaction() as tx:
            tx.execute("INSERT INTO notes (body) VALUES (?)", ("committed",))

        assert db.fetch_one("SELECT body FROM notes") == {"body": "committed"}

    def test_transaction_rolls_back_on_error(self, db: SQLiteDatabase) -> None:
        with pytest.raises(RuntimeError):
            with db.transaction() as tx:
                tx.execute("INSERT INTO notes (body) VALUES (?)", ("discarded",))
                raise RuntimeError("abort")

        assert db.fetch_all("SELECT id FROM notes") == []

    def test_sql_errors_are_translated(self, db: SQLiteDatabase) -> None:
        with pytest.raises(DatabaseError):
            db.fetch_all("SELECT * FROM table_that_does_not_exist")

    def test_healthcheck_reports_availability(self, tmp_path: Path) -> None:
        assert SQLiteDatabase(tmp_path / "app.db").healthcheck() is True
        assert SQLiteDatabase(tmp_path).healthcheck() is False


# ─── Research executor ──────────────────────────────────────────────────────


class TestLocalResearchExecutor:
    def test_builds_the_orchestrator_with_request_configuration(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict = {}

        class FakeOrchestrator:
            def run(self, topic: str):
                captured["topic"] = topic
                return {"status": "completed", "critic_score": 9}

        def fake_build(**kwargs):
            captured.update(kwargs)
            return FakeOrchestrator()

        monkeypatch.setattr(
            "app.orchestration.pipeline.build_orchestrator", fake_build
        )

        result = LocalResearchExecutor().execute(
            ResearchExecutionRequest(
                topic="AI in medicine",
                provider="groq",
                max_revisions=1,
                approval_threshold=7,
                output_dir="reports",
            ),
            user_id="user-1",
        )

        assert result["status"] == "completed"
        assert captured == {
            "provider": "groq",
            "max_revisions": 1,
            "approval_threshold": 7,
            "output_dir": "reports",
            "recorder": None,
            "topic": "AI in medicine",
        }

    def test_invalid_pipeline_state_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class BadOrchestrator:
            def run(self, topic: str):
                return "not-a-mapping"

        monkeypatch.setattr(
            "app.orchestration.pipeline.build_orchestrator",
            lambda **kwargs: BadOrchestrator(),
        )

        with pytest.raises(OrchestrationError):
            LocalResearchExecutor().execute(
                ResearchExecutionRequest(topic="Anything", provider="groq")
            )


class _RecordingSessions:
    def __init__(self) -> None:
        self.saved: list[dict] = []

    def save_session(self, state, **kwargs):
        self.saved.append({"state": state, **kwargs})
        return "session-1"


class _RecordingExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[ResearchExecutionRequest, str | None]] = []

    def execute(self, request, *, user_id=None):
        self.calls.append((request, user_id))
        return {
            "status": "completed",
            "critic_score": 9,
            "revision_count": 1,
            "saved_report_path": "reports/run.md",
            "search_results_raw": [{}, {}],
            "logs": ["done"],
        }


class TestResearchServiceDelegation:
    def test_research_service_uses_the_injected_executor(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def forbidden_build(**kwargs):
            raise AssertionError("ResearchService must not build the orchestrator itself")

        monkeypatch.setattr(
            "app.orchestration.pipeline.build_orchestrator", forbidden_build
        )

        executor = _RecordingExecutor()
        sessions = _RecordingSessions()
        service = ResearchService(get_settings(), sessions, executor)  # type: ignore[arg-type]

        event_queue: "queue.Queue" = queue.Queue()
        service.run_blocking(
            request=ResearchRunRequest(topic="AI in medicine", provider="groq"),
            user_id="user-7",
            event_queue=event_queue,
            research_id="res-1",
            cancel_event=threading.Event(),
        )

        events = []
        while True:
            item = event_queue.get(timeout=2)
            if item is None:
                break
            events.append(item)

        complete = [payload for kind, payload in events if kind == "complete"]
        assert complete and complete[0]["critic_score"] == 9
        assert complete[0]["sources_analyzed"] == 2

        ((request, user_id),) = executor.calls
        assert request.topic == "AI in medicine"
        assert request.provider == "groq"
        assert user_id == "user-7"
        assert sessions.saved[0]["user_id"] == "user-7"


# ─── Container wiring ───────────────────────────────────────────────────────


@pytest.fixture
def container(tmp_path: Path):
    settings = dataclasses.replace(
        get_settings(),
        reports_dir=tmp_path / "reports",
        exports_dir=tmp_path / "reports" / "exports",
        sessions_db_path=tmp_path / "sessions.db",
    )
    service_container = ServiceContainer(settings)
    try:
        yield service_container
    finally:
        service_container.shutdown()


class TestServiceContainerInfrastructure:
    def test_local_adapters_are_the_defaults(self, container: ServiceContainer) -> None:
        assert isinstance(container.cache, LocalCacheService)
        assert isinstance(container.database, SQLiteDatabase)
        assert isinstance(container.jobs, LocalJobQueue)
        assert isinstance(container.storage, LocalObjectStorage)
        assert isinstance(container.events, InProcessEventBus)
        assert isinstance(container.research_executor, LocalResearchExecutor)

    def test_instances_are_cached(self, container: ServiceContainer) -> None:
        assert container.cache is container.cache
        assert container.events is container.events
        assert container.jobs is container.jobs

    def test_storage_lives_beside_reports(self, container: ServiceContainer) -> None:
        assert container.storage.root == container.settings.reports_dir / "objects"

    def test_research_service_receives_the_container_executor(
        self, container: ServiceContainer
    ) -> None:
        assert container.research._executor is container.research_executor

    def test_injected_ports_override_local_defaults(self, tmp_path: Path) -> None:
        settings = dataclasses.replace(
            get_settings(),
            reports_dir=tmp_path / "reports",
            exports_dir=tmp_path / "reports" / "exports",
            sessions_db_path=tmp_path / "sessions.db",
        )
        cache = LocalCacheService()
        bus = InProcessEventBus()
        jobs = LocalJobQueue()
        service_container = ServiceContainer(
            settings, cache=cache, events=bus, job_queue=jobs
        )
        try:
            assert service_container.cache is cache
            assert service_container.events is bus
            assert service_container.jobs is jobs
        finally:
            service_container.shutdown()

    def test_shutdown_closes_the_local_queue_and_is_idempotent(
        self, container: ServiceContainer
    ) -> None:
        jobs = container.jobs
        jobs.register("noop", lambda ctx, payload: None)

        container.shutdown()
        container.shutdown()

        with pytest.raises(RuntimeError):
            jobs.enqueue("noop")
