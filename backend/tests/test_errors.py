"""
tests/test_errors.py
====================
Error handling: status codes, payload shape and non-disclosure guarantees.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.errors import (
    AuthenticationError,
    AuthorizationError,
    DatabaseError,
    LLMProviderError,
    NotFoundError,
    ValidationError,
    install_exception_handlers,
)


def build_app() -> FastAPI:
    application = FastAPI()
    install_exception_handlers(application)

    @application.get("/database")
    async def _database():
        raise DatabaseError("Could not load research sessions.")

    @application.get("/not-found")
    async def _not_found():
        raise NotFoundError()

    @application.get("/auth")
    async def _auth():
        raise AuthenticationError()

    @application.get("/forbidden")
    async def _forbidden():
        raise AuthorizationError()

    @application.get("/invalid")
    async def _invalid():
        raise ValidationError("topic is required")

    @application.get("/llm")
    async def _llm():
        raise LLMProviderError()

    @application.get("/boom")
    async def _boom():
        raise RuntimeError("postgresql://user:supersecret@db.internal/research")

    return application


def client() -> TestClient:
    return TestClient(build_app(), raise_server_exceptions=False)


class TestErrorPayloads:
    def test_database_error_uses_503(self) -> None:
        response = client().get("/database")
        assert response.status_code == 503
        body = response.json()
        assert body["detail"] == "Could not load research sessions."
        assert body["error_code"] == "database_unavailable"
        assert "request_id" in body

    def test_not_found(self) -> None:
        response = client().get("/not-found")
        assert response.status_code == 404
        assert response.json()["error_code"] == "not_found"

    def test_authentication_error(self) -> None:
        response = client().get("/auth")
        assert response.status_code == 401
        assert response.json()["error_code"] == "authentication_required"

    def test_authorization_error(self) -> None:
        response = client().get("/forbidden")
        assert response.status_code == 403

    def test_validation_error_detail_is_preserved(self) -> None:
        response = client().get("/invalid")
        assert response.status_code == 422
        assert response.json()["detail"] == "topic is required"

    def test_llm_error_is_503(self) -> None:
        response = client().get("/llm")
        assert response.status_code == 503
        assert response.json()["error_code"] == "llm_provider_unavailable"


class TestNonDisclosure:
    def test_unhandled_exception_hides_internals(self) -> None:
        response = client().get("/boom")
        assert response.status_code == 500
        body = response.json()
        assert body["error_code"] == "internal_error"
        assert "supersecret" not in response.text
        assert "postgresql" not in response.text
        assert body["detail"] == "An unexpected server error occurred. Please try again."

    def test_request_id_is_returned(self) -> None:
        response = client().get("/database")
        assert response.json()["request_id"]
