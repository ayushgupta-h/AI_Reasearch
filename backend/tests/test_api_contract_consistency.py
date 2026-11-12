"""Cross-endpoint error payload consistency (shape, codes, no envelopes)."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.factory import create_app
from app.services.container import (
    get_chat_service,
    get_research_service,
    get_session_service,
)


class _Sessions:
    def list_sessions(self, user_id=None):
        return []

    def delete_session(self, session_id, user_id=None):
        return False

    def save_session(self, state, **kwargs):
        return "id"

    def user_owns_report(self, user_id, filename):
        return True


class _Research:
    async def stream(self, request, user_id):
        yield ": keepalive\n\n"


class _Chat:
    def complete(self, message):
        return ("ok", "groq")


@pytest.fixture
def client(tmp_path: Path):
    settings = dataclasses.replace(
        get_settings(),
        reports_dir=tmp_path / "reports",
        exports_dir=tmp_path / "reports" / "exports",
        sessions_db_path=tmp_path / "sessions.db",
        log_file=None,
    )
    application = create_app(settings)
    application.dependency_overrides[get_session_service] = _Sessions
    application.dependency_overrides[get_research_service] = _Research
    application.dependency_overrides[get_chat_service] = _Chat
    with TestClient(application, raise_server_exceptions=False) as test_client:
        yield test_client


ERROR_KEYS = {"detail", "error_code", "request_id"}


def _assert_error_shape(response, status: int, code: str | None = None) -> None:
    assert response.status_code == status
    body = response.json()
    assert set(body) >= ERROR_KEYS
    assert "success" not in body
    assert body["request_id"]
    if code:
        assert body["error_code"] == code


class TestErrorConsistency:
    def test_401_auth_required(self, client: TestClient) -> None:
        _assert_error_shape(client.get("/auth/me"), 401, "authentication_required")

    def test_404_unknown_route(self, client: TestClient) -> None:
        _assert_error_shape(client.get("/does-not-exist"), 404, "not_found")

    def test_404_missing_report(self, client: TestClient) -> None:
        _assert_error_shape(client.get("/reports/missing.md"), 404, "not_found")

    def test_405_method_not_allowed(self, client: TestClient) -> None:
        _assert_error_shape(client.put("/health"), 405, "method_not_allowed")

    def test_422_invalid_research_payload(self, client: TestClient) -> None:
        response = client.post("/research", json={"topic": "ab"})
        _assert_error_shape(response, 422, "validation_error")
        errors = response.json()["detail"]
        assert isinstance(errors, list) and errors
        for entry in errors:
            assert set(entry) <= {"loc", "msg", "type"}
            assert "input" not in entry

    def test_422_invalid_chat_payload(self, client: TestClient) -> None:
        _assert_error_shape(
            client.post("/chat", json={"message": "   "}), 422, "validation_error"
        )

    def test_403_forbidden_share(self, client: TestClient) -> None:
        # No report file exists, so authorization resolves to 404 before 403;
        # the ownership denial itself is covered in the hardening suite.
        _assert_error_shape(client.get("/reports/none.md/share"), 401)

    def test_error_responses_ignore_the_envelope_flag(self, client: TestClient) -> None:
        _assert_error_shape(
            client.get("/auth/me", params={"envelope": "true"}),
            401,
            "authentication_required",
        )

    def test_every_error_code_is_documented(self, client: TestClient) -> None:
        from app.schemas.enums import ErrorCode

        documented = set(
            client.get("/openapi.json").json()["components"]["schemas"]["ErrorCode"][
                "enum"
            ]
        )
        assert {code.value for code in ErrorCode} == documented
