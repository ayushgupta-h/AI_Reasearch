"""
tests/test_api_endpoints.py
===========================
End-to-end HTTP tests for the rewritten API layer.

The public contract (paths, payload keys, SSE event names, status codes) must
stay identical to the pre-refactor server. External services are replaced with
fakes, so no API keys or network access are required.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.factory import create_app
from app.services.container import (
    get_chat_service,
    get_report_service,
    get_research_service,
    get_session_service,
)
from app.services.report_service import ReportService
from app.utils.sse import build_event


class FakeSessionService:
    def list_sessions(self, user_id=None):
        return [
            {
                "session_id": "s1",
                "topic": "AI in healthcare",
                "timestamp": "2026-01-01T00:00:00",
                "provider": "groq",
                "critic_score": 9,
                "revision_count": 1,
                "status": "completed",
                "saved_report_path": "/reports/r.md",
                "citations_verified": 4,
                "citations_total": 5,
                "duration_seconds": 12.5,
                "user_id": user_id,
            }
        ]

    def delete_session(self, session_id, user_id=None):
        return False

    def save_session(self, state, **kwargs):
        return "generated-id"

    def user_owns_report(self, user_id, filename):
        return True


class FakeResearchService:
    """Streams a realistic event sequence without running the pipeline."""

    async def stream(self, request, user_id):
        yield build_event(
            "progress",
            "Research pipeline initialising...",
            data={"topic": request.topic, "research_id": "run-1"},
        )
        yield build_event(
            "agent_start", "Agent active: Strategist", agent="Strategist"
        )
        yield build_event(
            "complete",
            "Research report successfully generated.",
            data={
                "status": "completed",
                "provider": "groq",
                "critic_score": 9,
                "revision_count": 0,
                "saved_report_path": "/reports/r.md",
                "sources_analyzed": 3,
                "logs": [],
                "research_id": "run-1",
            },
        )


class FakeChatService:
    def complete(self, message):
        return ("Hello from Quest", "groq")


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
def client(api_settings):
    application = create_app(api_settings)
    application.dependency_overrides[get_session_service] = FakeSessionService
    application.dependency_overrides[get_research_service] = FakeResearchService
    application.dependency_overrides[get_chat_service] = FakeChatService
    application.dependency_overrides[get_report_service] = lambda: ReportService(
        api_settings, FakeSessionService()  # type: ignore[arg-type]
    )
    with TestClient(application, raise_server_exceptions=False) as test_client:
        yield test_client


class TestSystemEndpoints:
    def test_health(self, client: TestClient) -> None:
        response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["version"]
        assert response.headers.get("x-request-id")

    def test_providers(self, client: TestClient) -> None:
        response = client.get("/providers")
        assert response.status_code == 200
        body = response.json()
        assert body["providers"] == ["groq", "gemini", "mistral", "huggingface"]
        assert body["default"] in body["providers"]

    def test_openapi_contract_paths(self, client: TestClient) -> None:
        paths = client.get("/openapi.json").json()["paths"]
        for expected in (
            "/health",
            "/providers",
            "/auth/config",
            "/auth/me",
            "/sessions",
            "/sessions/{session_id}",
            "/reports/{filename}",
            "/reports/{filename}/share",
            "/export",
            "/research",
            "/chat",
        ):
            assert expected in paths, f"missing contract path {expected}"


class TestAuth:
    def test_auth_me_requires_token(self, client: TestClient) -> None:
        response = client.get("/auth/me")
        assert response.status_code == 401
        assert response.json()["error_code"] == "authentication_required"

    def test_delete_session_requires_token(self, client: TestClient) -> None:
        response = client.delete("/sessions/s1")
        assert response.status_code == 401

    def test_auth_config_shape(self, client: TestClient) -> None:
        body = client.get("/auth/config").json()
        assert set(body) == {"supabaseUrl", "supabaseAnonKey"}


class TestSessions:
    def test_sessions_anonymous_shape(self, client: TestClient) -> None:
        response = client.get("/sessions")
        assert response.status_code == 200
        body = response.json()
        assert body["count"] == 1
        session = body["sessions"][0]
        # Keys consumed by history.html / dashboard.html / new.html
        for key in (
            "session_id",
            "topic",
            "timestamp",
            "provider",
            "critic_score",
            "status",
            "saved_report_path",
        ):
            assert key in session


class TestResearchValidation:
    @pytest.mark.parametrize(
        "payload",
        [
            {"topic": "ab"},
            {"topic": "   "},
            {"topic": "Valid topic", "provider": "openai"},
            {"topic": "Valid topic", "max_revisions": 9},
            {"topic": "Valid topic", "approval_threshold": 0},
            {"topic": "Valid topic", "output_dir": "/etc"},
        ],
    )
    def test_invalid_payloads_return_422(self, client: TestClient, payload) -> None:
        response = client.post("/research", json=payload)
        assert response.status_code == 422
        assert response.json()["error_code"] == "validation_error"


class TestResearchStreaming:
    def test_research_streams_sse_events(self, client: TestClient) -> None:
        response = client.post(
            "/research", json={"topic": "Quantum computing breakthroughs"}
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        body = response.text
        assert "event: progress" in body
        assert "event: agent_start" in body
        assert "event: complete" in body
        assert "Research report successfully generated." in body
        assert '"critic_score": 9' in body

    def test_sse_frames_are_well_formed(self, client: TestClient) -> None:
        body = client.post(
            "/research", json={"topic": "Solid state batteries"}
        ).text
        frames = [frame for frame in body.split("\n\n") if frame.strip()]
        assert frames
        for frame in frames:
            assert frame.startswith("event: ") or frame.startswith(":")
            assert "\ndata: " in frame or frame.startswith(":")


class TestReports:
    def _write_report(self, settings, name: str = "demo.md") -> str:
        path = settings.reports_dir / name
        path.write_text("# Demo report\n\nContent.", encoding="utf-8")
        return name

    def test_missing_report_returns_404(self, client: TestClient) -> None:
        response = client.get("/reports/missing.md")
        assert response.status_code == 404
        assert response.json()["error_code"] == "not_found"

    def test_share_requires_authentication(self, client: TestClient, api_settings) -> None:
        name = self._write_report(api_settings)
        response = client.get(f"/reports/{name}/share")
        assert response.status_code == 401
        assert response.json()["error_code"] == "authentication_required"

    def test_share_then_download_read_only(
        self, client: TestClient, api_settings
    ) -> None:
        from app.security.dependencies import get_current_user

        name = self._write_report(api_settings)
        client.app.dependency_overrides[get_current_user] = lambda: {"user_id": "u-1"}
        try:
            share = client.get(f"/reports/{name}/share")
        finally:
            client.app.dependency_overrides.pop(get_current_user, None)
        assert share.status_code == 200
        token = share.json()["share_token"]

        download = client.get(f"/reports/{name}", params={"share": token})
        assert download.status_code == 200
        assert download.headers["x-read-only"] == "true"
        assert "Demo report" in download.text

    def test_download_without_auth_or_share_is_401(
        self, client: TestClient, api_settings
    ) -> None:
        name = self._write_report(api_settings)
        response = client.get(f"/reports/{name}")
        assert response.status_code == 401

    def test_markdown_export(self, client: TestClient) -> None:
        response = client.post(
            "/export",
            json={"content": "# Report\n\nBody text.", "topic": "AI Safety", "format": "md"},
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/markdown")
        assert "quest_ai_safety_" in response.headers["content-disposition"]

    def test_empty_export_is_400(self, client: TestClient) -> None:
        # Schema allows whitespace-only content; the service rejects it.
        response = client.post("/export", json={"content": "   ", "format": "md"})
        assert response.status_code == 400
        assert response.json()["error_code"] == "bad_request"


class TestChat:
    def test_chat_returns_reply(self, client: TestClient) -> None:
        response = client.post("/chat", json={"message": "What is Quest?"})
        assert response.status_code == 200
        assert response.json() == {"reply": "Hello from Quest", "provider": "groq"}

    def test_chat_rejects_empty_message(self, client: TestClient) -> None:
        assert client.post("/chat", json={"message": "   "}).status_code == 422


class TestResponseEnvelopeAndPagination:
    """The envelope is opt-in; legacy payload shapes must stay untouched."""

    def test_health_envelope(self, client: TestClient) -> None:
        body = client.get("/health", params={"envelope": "true"}).json()
        assert set(body) == {"success", "data", "message", "request_id"}
        assert body["success"] is True
        assert body["data"]["status"] == "ok"
        assert body["request_id"]

    def test_health_default_is_flat(self, client: TestClient) -> None:
        body = client.get("/health").json()
        assert "success" not in body
        assert body["status"] == "ok"

    def test_providers_include_non_secret_statuses(self, client: TestClient) -> None:
        body = client.get("/providers").json()
        assert [item["provider"] for item in body["statuses"]] == body["providers"]
        assert all(item["fallback_rank"] >= 1 for item in body["statuses"])
        assert "api_key" not in str(body).lower()

    def test_providers_envelope(self, client: TestClient) -> None:
        body = client.get("/providers", params={"envelope": "true"}).json()
        assert body["success"] is True
        assert body["data"]["providers"] == [
            "groq",
            "gemini",
            "mistral",
            "huggingface",
        ]

    def test_sessions_pagination_metadata(self, client: TestClient) -> None:
        body = client.get("/sessions", params={"limit": 1, "offset": 0}).json()
        assert body["count"] == 1
        assert body["pagination"] == {
            "limit": 1,
            "offset": 0,
            "total": 1,
            "returned": 1,
            "has_more": False,
        }

    def test_sessions_without_pagination_has_no_key(self, client: TestClient) -> None:
        body = client.get("/sessions").json()
        assert "pagination" not in body
        assert body["count"] == 1

    def test_sessions_envelope(self, client: TestClient) -> None:
        body = client.get("/sessions", params={"limit": 1, "envelope": "true"}).json()
        assert body["success"] is True
        assert body["data"]["count"] == 1
        assert body["data"]["pagination"]["total"] == 1

    @pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"offset": -1}])
    def test_sessions_rejects_invalid_pagination(
        self, client: TestClient, params: dict
    ) -> None:
        assert client.get("/sessions", params=params).status_code == 422


class TestEntrypoint:
    def test_server_module_exposes_app(self) -> None:
        import server

        assert server.app.title == "Research AI API"
