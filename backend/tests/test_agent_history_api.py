"""
tests/test_agent_history_api.py
===============================
Agent execution history: persistence, recorder, timeline, progress, ownership.

History is always produced by the real recorder (or by instrument_node calling
it), never hand-crafted in the API layer.
"""

from __future__ import annotations

import dataclasses
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.factory import create_app
from app.infrastructure import LocalResearchExecutor, ResearchExecutionRequest, SQLiteDatabase
from app.orchestration.instrumentation import instrument_node
from app.repositories.agent_history import AgentHistoryRepository
from app.security.dependencies import get_current_user
from app.services.agent_history_service import AgentExecutionRecorder, AgentHistoryService


@pytest.fixture
def api_settings(tmp_path: Path):
    base = get_settings()
    return dataclasses.replace(
        base,
        reports_dir=tmp_path / "reports",
        exports_dir=tmp_path / "reports" / "exports",
        sessions_db_path=tmp_path / "sessions.db",
        log_file=None,
    )


@pytest.fixture
def history(api_settings) -> AgentHistoryService:
    return AgentHistoryService(
        AgentHistoryRepository(SQLiteDatabase(api_settings.sessions_db_path))
    )


@pytest.fixture
def user_state() -> dict:
    return {"user_id": "user-a"}


@pytest.fixture
def client(api_settings, user_state):
    application = create_app(api_settings)
    application.dependency_overrides[get_current_user] = lambda: dict(user_state)
    with TestClient(application, raise_server_exceptions=False) as test_client:
        test_client.user_state = user_state  # type: ignore[attr-defined]
        yield test_client


@pytest.fixture
def anonymous_client(api_settings):
    application = create_app(api_settings)
    with TestClient(application, raise_server_exceptions=False) as test_client:
        yield test_client


def _start_run(
    history: AgentHistoryService, owner: str = "user-a", topic: str = "AI in medicine"
) -> tuple[str, AgentExecutionRecorder]:
    research_id = uuid.uuid4().hex
    history.register_research(research_id, owner, topic)
    recorder = history.recorder_for(research_id, owner, topic)
    assert recorder is not None
    return research_id, recorder


def _record(
    recorder: AgentExecutionRecorder,
    agent: str = "planner",
    *,
    status: str = "completed",
    duration_ms: float = 12.5,
    output: str = "state_update",
    error: str | None = None,
) -> str:
    execution_id = recorder.started(agent)
    assert execution_id is not None
    recorder.finished(
        execution_id,
        agent_name=agent,
        status=status,
        duration_ms=duration_ms,
        output_reference=output,
        error_summary=error,
    )
    return execution_id


# ─── Executions ─────────────────────────────────────────────────────────────


class TestAgentExecutions:
    def test_list_returns_recorded_history(
        self, client: TestClient, history: AgentHistoryService
    ) -> None:
        research_id, recorder = _start_run(history)
        execution_id = _record(recorder, "planner", output="research_plan")

        body = client.get(f"/research/{research_id}/agents").json()

        assert body["research_id"] == research_id
        assert body["count"] == 1
        execution = body["executions"][0]
        assert execution["execution_id"] == execution_id
        assert execution["agent_name"] == "planner"
        assert execution["status"] == "completed"
        assert execution["duration_ms"] == 12.5
        assert execution["retry_count"] == 0
        assert execution["output_reference"] == "research_plan"
        assert execution["finished_at"].endswith("Z")
        assert body["pagination"]["total"] == 1

    def test_detail_returns_one_execution(
        self, client: TestClient, history: AgentHistoryService
    ) -> None:
        research_id, recorder = _start_run(history)
        execution_id = _record(recorder, "search")

        response = client.get(f"/research/{research_id}/agents/{execution_id}")

        assert response.status_code == 200
        assert response.json()["execution_id"] == execution_id

    def test_filters_pagination_and_sorting(
        self, client: TestClient, history: AgentHistoryService
    ) -> None:
        research_id, recorder = _start_run(history)
        _record(recorder, "planner")
        _record(recorder, "search", status="failed", error="provider unavailable")
        _record(recorder, "writer")

        failed = client.get(
            f"/research/{research_id}/agents", params={"status": "failed"}
        ).json()
        assert [item["agent_name"] for item in failed["executions"]] == ["search"]

        by_name = client.get(
            f"/research/{research_id}/agents", params={"agent_name": "writer"}
        ).json()
        assert by_name["count"] == 1

        page = client.get(
            f"/research/{research_id}/agents",
            params={"limit": 1, "offset": 1, "sort": "agent_name", "order": "asc"},
        ).json()
        assert page["pagination"]["total"] == 3
        assert page["pagination"]["has_more"] is True
        assert page["count"] == 1

    def test_retry_count_increments_for_repeated_agent(
        self, client: TestClient, history: AgentHistoryService
    ) -> None:
        research_id, recorder = _start_run(history)
        _record(recorder, "refiner")
        _record(recorder, "refiner")

        body = client.get(f"/research/{research_id}/agents").json()
        retries = sorted(item["retry_count"] for item in body["executions"])
        assert retries == [0, 1]

    def test_error_summary_is_redacted(
        self, client: TestClient, history: AgentHistoryService
    ) -> None:
        research_id, recorder = _start_run(history)
        secret = "sk-" + "a" * 40
        _record(recorder, "critic", status="failed", error=f"provider key {secret}")

        body = client.get(f"/research/{research_id}/agents").json()
        summary = body["executions"][0]["error_summary"]
        assert summary and "RuntimeError" not in summary
        assert secret not in summary
        assert len(summary) <= 300


# ─── Timeline ───────────────────────────────────────────────────────────────


class TestResearchTimeline:
    def test_timeline_is_chronological(
        self, client: TestClient, history: AgentHistoryService
    ) -> None:
        research_id, recorder = _start_run(history)
        _record(recorder, "planner")
        _record(recorder, "search")
        history.complete_run(research_id, "user-a")

        body = client.get(f"/research/{research_id}/timeline").json()
        types = [event["event_type"] for event in body["events"]]

        assert types[0] == "research_created"
        assert types[1] == "research_started"
        assert types.index("agent_started") < types.index("agent_completed")
        assert types[-1] == "research_completed"
        assert body["pagination"]["total"] == len(types)

    def test_timeline_filters_and_pagination(
        self, client: TestClient, history: AgentHistoryService
    ) -> None:
        research_id, recorder = _start_run(history)
        _record(recorder, "planner")
        _record(recorder, "search")

        completed = client.get(
            f"/research/{research_id}/timeline", params={"event_type": "agent_completed"}
        ).json()
        assert completed["count"] == 2

        page = client.get(
            f"/research/{research_id}/timeline", params={"limit": 1, "offset": 1}
        ).json()
        assert page["count"] == 1
        assert page["pagination"]["has_more"] is True

    def test_project_research_creation_seeds_the_timeline(
        self, client: TestClient
    ) -> None:
        project = client.post("/projects", json={"name": "Timeline project"}).json()
        research = client.post(
            f"/projects/{project['project_id']}/research",
            json={"topic": "Agent observability"},
        ).json()

        body = client.get(f"/research/{research['research_id']}/timeline").json()
        assert body["count"] == 1
        assert body["events"][0]["event_type"] == "research_created"


# ─── Progress ───────────────────────────────────────────────────────────────


class TestResearchProgress:
    def test_progress_counts_and_current_agent(
        self, client: TestClient, history: AgentHistoryService
    ) -> None:
        research_id, recorder = _start_run(history)
        _record(recorder, "planner")
        recorder.started("search")  # still running

        body = client.get(f"/research/{research_id}/progress").json()

        assert body["status"] == "running"
        assert body["total_executions"] == 2
        assert body["completed_executions"] == 1
        assert body["running_executions"] == 1
        assert body["failed_executions"] == 0
        assert body["percent_complete"] == 50.0
        assert body["current_agent"] == "search"
        assert body["latest_event"] == "search execution started."

    def test_progress_after_completion(
        self, client: TestClient, history: AgentHistoryService
    ) -> None:
        research_id, recorder = _start_run(history)
        _record(recorder, "planner")
        history.complete_run(research_id, "user-a")

        body = client.get(f"/research/{research_id}/progress").json()
        assert body["status"] == "completed"
        assert body["percent_complete"] == 100.0
        assert body["current_agent"] is None


# ─── Ownership and validation ───────────────────────────────────────────────


class TestAccessControl:
    def test_cross_user_access_is_hidden(
        self, client: TestClient, history: AgentHistoryService
    ) -> None:
        research_id, recorder = _start_run(history)
        execution_id = _record(recorder, "planner")
        client.user_state["user_id"] = "user-b"

        for path in (
            f"/research/{research_id}/agents",
            f"/research/{research_id}/agents/{execution_id}",
            f"/research/{research_id}/timeline",
            f"/research/{research_id}/progress",
        ):
            assert client.get(path).status_code == 404, path

    def test_unknown_research_and_execution_are_404(
        self, client: TestClient, history: AgentHistoryService
    ) -> None:
        research_id, recorder = _start_run(history)

        assert client.get(f"/research/{uuid.uuid4().hex}/agents").status_code == 404
        assert (
            client.get(
                f"/research/{research_id}/agents/{uuid.uuid4().hex}"
            ).status_code
            == 404
        )

    @pytest.mark.parametrize(
        "path",
        [
            "/research/not-an-id/agents",
            f"/research/{'a' * 32}/agents/not-an-id",
            "/research/not-an-id/timeline",
            "/research/not-an-id/progress",
        ],
    )
    def test_malformed_ids_are_422(self, client: TestClient, path: str) -> None:
        assert client.get(path).status_code == 422

    @pytest.mark.parametrize(
        "path",
        [
            f"/research/{uuid.uuid4().hex}/agents",
            f"/research/{uuid.uuid4().hex}/agents/{uuid.uuid4().hex}",
            f"/research/{uuid.uuid4().hex}/timeline",
            f"/research/{uuid.uuid4().hex}/progress",
        ],
    )
    def test_endpoints_require_authentication(
        self, anonymous_client: TestClient, path: str
    ) -> None:
        assert anonymous_client.get(path).status_code == 401


# ─── Recording from real execution paths ────────────────────────────────────


class TestRecorderIntegration:
    def test_instrumented_node_records_completion(
        self, history: AgentHistoryService
    ) -> None:
        research_id, recorder = _start_run(history)
        wrapped = instrument_node(
            "planner", lambda state: {"report": "# Draft"}, recorder
        )

        assert wrapped({"topic": "x"}) == {"report": "# Draft"}

        rows, _ = history.list_executions(research_id, "user-a")
        assert rows[0]["status"] == "completed"
        assert rows[0]["output_reference"] == "report_draft"
        assert rows[0]["duration_ms"] is not None and rows[0]["duration_ms"] >= 0

    def test_instrumented_node_records_failure_without_secrets(
        self, history: AgentHistoryService
    ) -> None:
        research_id, recorder = _start_run(history)
        secret = "sk-" + "b" * 40

        def explode(state):
            raise RuntimeError(f"provider rejected key {secret}")

        with pytest.raises(RuntimeError, match="provider rejected"):
            instrument_node("critic", explode, recorder)({})

        rows, _ = history.list_executions(research_id, "user-a")
        assert rows[0]["status"] == "failed"
        assert rows[0]["error_summary"].startswith("RuntimeError")
        assert secret not in rows[0]["error_summary"]

    def test_executor_attaches_a_recorder(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict = {}

        class FakeOrchestrator:
            def run(self, topic: str):
                return {"status": "completed"}

        def fake_build(**kwargs):
            captured.update(kwargs)
            return FakeOrchestrator()

        monkeypatch.setattr(
            "app.orchestration.pipeline.build_orchestrator", fake_build
        )
        sentinel = object()
        executor = LocalResearchExecutor(
            recorder_factory=lambda research_id, owner, topic: sentinel
        )

        executor.execute(
            ResearchExecutionRequest(
                topic="Observability",
                provider="groq",
                research_id="a" * 32,
            ),
            user_id="user-a",
        )
        assert captured["recorder"] is sentinel

    def test_anonymous_runs_are_not_recorded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict = {}

        class FakeOrchestrator:
            def run(self, topic: str):
                return {"status": "completed"}

        def fake_build(**kwargs):
            captured.update(kwargs)
            return FakeOrchestrator()

        monkeypatch.setattr(
            "app.orchestration.pipeline.build_orchestrator", fake_build
        )
        executor = LocalResearchExecutor(recorder_factory=lambda *args: object())

        executor.execute(
            ResearchExecutionRequest(
                topic="Observability", provider="groq", research_id="b" * 32
            ),
            user_id=None,
        )
        assert captured["recorder"] is None

    def test_duration_and_timestamps_are_timezone_aware(
        self, client: TestClient, history: AgentHistoryService
    ) -> None:
        research_id, recorder = _start_run(history)
        _record(recorder, "planner")

        body = client.get(f"/research/{research_id}/agents").json()
        started = datetime.fromisoformat(
            body["executions"][0]["started_at"].replace("Z", "+00:00")
        )
        assert started.tzinfo is not None
        assert started <= datetime.now(timezone.utc)
