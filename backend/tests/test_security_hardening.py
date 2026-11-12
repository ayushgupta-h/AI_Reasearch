"""Security hardening tests: share tokens, file paths, ownership, CORS, logging."""

from __future__ import annotations

import dataclasses
import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.errors import (
    AppError,
    AuthenticationError,
    AuthorizationError,
    BadRequestError,
    NotFoundError,
    VectorStoreError,
)
from app.factory import create_app
from app.observability import JsonFormatter, _ContextFilter, log_context, redact
from app.security import share_tokens
from app.security.share_tokens import (
    TOKEN_LENGTH,
    generate_share_token,
    token_expires_at,
    verify_share_token,
)
from app.services.report_service import ReportService


class _Sessions:
    """Ownership double with real credential semantics (no secrets involved)."""

    def __init__(self, owner: str | None = "user-1") -> None:
        self.owner = owner

    def user_owns_report(self, user_id: str, filename: str) -> bool:
        return self.owner is not None and user_id == self.owner


@pytest.fixture
def report_settings(tmp_path: Path):
    settings = dataclasses.replace(
        get_settings(),
        reports_dir=tmp_path / "reports",
        exports_dir=tmp_path / "reports" / "exports",
        log_file=None,
    )
    settings.ensure_directories()
    return settings


@pytest.fixture
def report_service(report_settings) -> ReportService:
    return ReportService(report_settings, _Sessions())  # type: ignore[arg-type]


# ─── File path policy ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "filename",
    [
        "../../etc/passwd",
        "..\\..\\windows\\win.ini",
        "/etc/passwd",
        "..%2f..%2fetc%2fpasswd",
        "nested/../../escape.md",
    ],
)
def test_traversal_and_foreign_paths_are_rejected(
    report_service: ReportService, filename: str
) -> None:
    with pytest.raises(AppError):
        report_service.resolve(filename)


def test_hidden_files_are_rejected(report_service: ReportService, report_settings) -> None:
    (report_settings.reports_dir / ".env").write_text("SECRET=1", encoding="utf-8")
    with pytest.raises(BadRequestError):
        report_service.resolve(".env")


def test_only_report_extensions_are_served(
    report_service: ReportService, report_settings
) -> None:
    (report_settings.reports_dir / "secrets.json").write_text("{}", encoding="utf-8")
    with pytest.raises(NotFoundError):
        report_service.resolve("secrets.json")


def test_nested_report_paths_inside_reports_are_allowed(
    report_service: ReportService, report_settings
) -> None:
    target = report_settings.reports_dir / "exports" / "nested.md"
    target.write_text("# Nested", encoding="utf-8")
    assert report_service.resolve("exports/nested.md") == target.resolve()


def test_symlinks_escaping_the_reports_directory_are_rejected(
    report_service: ReportService, report_settings, tmp_path: Path
) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text("secret", encoding="utf-8")
    link = report_settings.reports_dir / "link.md"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):  # pragma: no cover - platform policy
        pytest.skip("symlinks are not permitted on this host")
    with pytest.raises(AuthorizationError):
        report_service.resolve("link.md")


# ─── Share token signing ─────────────────────────────────────────────────────


def test_share_token_carries_a_future_expiry() -> None:
    token = generate_share_token("report_a.md")
    assert len(token) == TOKEN_LENGTH == 24
    expiry = token_expires_at(token)
    assert expiry is not None
    assert expiry > 0
    int(token[:8], 16)  # expiry segment is valid hex


def test_share_token_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    now = 1_800_000_000.0
    monkeypatch.setattr(share_tokens.time, "time", lambda: now)
    token = generate_share_token("report_a.md")
    assert verify_share_token("report_a.md", token) is True

    later = now + (get_settings().share_token_ttl_hours + 2) * 3600
    assert verify_share_token("report_a.md", token, now=later) is False


def test_share_token_is_bound_to_the_filename() -> None:
    token = generate_share_token("report_a.md")
    assert verify_share_token("report_a.md", token) is True
    assert verify_share_token("report_b.md", token) is False


def test_tampered_token_is_rejected() -> None:
    token = generate_share_token("report_a.md")
    tampered = token[:-1] + ("0" if token[-1] != "0" else "1")
    assert verify_share_token("report_a.md", tampered) is False


def test_verification_uses_constant_time_comparison(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    original = share_tokens.hmac.compare_digest

    def spy(left: str, right: str) -> bool:
        calls.append((left, right))
        return original(left, right)

    monkeypatch.setattr(share_tokens.hmac, "compare_digest", spy)
    token = generate_share_token("report_a.md")
    assert verify_share_token("report_a.md", token) is True
    assert calls
    assert all(len(left) == len(right) == TOKEN_LENGTH for left, right in calls)


def test_legacy_non_expiring_token_fails_closed() -> None:
    # Old tokens were 24 raw HMAC hex chars with no expiry segment.
    assert verify_share_token("report_a.md", "a" * 24) is False


# ─── Ownership & authorization ───────────────────────────────────────────────


def test_share_link_requires_authentication(
    report_service: ReportService, report_settings
) -> None:
    (report_settings.reports_dir / "r.md").write_text("# R", encoding="utf-8")
    with pytest.raises(AuthenticationError):
        report_service.share_link("r.md", user=None)


def test_share_link_requires_ownership(
    report_service: ReportService, report_settings
) -> None:
    (report_settings.reports_dir / "r.md").write_text("# R", encoding="utf-8")
    with pytest.raises(AuthorizationError):
        report_service.share_link("r.md", user={"user_id": "intruder"})


def test_download_denied_for_non_owner(report_settings) -> None:
    (report_settings.reports_dir / "r.md").write_text("# R", encoding="utf-8")
    service = ReportService(report_settings, _Sessions(owner="owner-1"))  # type: ignore[arg-type]
    with pytest.raises(AuthorizationError):
        service.authorize_download("r.md", user={"user_id": "intruder"}, share_token=None)


def test_download_allowed_for_owner(report_settings) -> None:
    (report_settings.reports_dir / "r.md").write_text("# R", encoding="utf-8")
    service = ReportService(report_settings, _Sessions(owner="user-1"))  # type: ignore[arg-type]
    path, media_type, read_only = service.authorize_download(
        "r.md", user={"user_id": "user-1"}, share_token=None
    )
    assert path.is_file()
    assert media_type.startswith("text/markdown")
    assert read_only is False


# ─── Error payload hygiene ───────────────────────────────────────────────────


@pytest.fixture
def leak_client(tmp_path: Path):
    settings = dataclasses.replace(
        get_settings(),
        app_env="test",
        reports_dir=tmp_path / "reports",
        exports_dir=tmp_path / "reports" / "exports",
        log_file=None,
    )
    application = create_app(settings)

    @application.get("/leak")
    async def _leak():
        raise VectorStoreError("chroma key sk-proj-abcdefghijklmnop rejected")

    with TestClient(application, raise_server_exceptions=False) as client:
        yield client


def test_domain_error_messages_are_redacted(leak_client: TestClient) -> None:
    response = leak_client.get("/leak")
    assert response.status_code == 500
    body = response.json()
    assert body["error_code"] == "vector_store_failed"
    assert "sk-proj" not in response.text
    assert "***" in body["detail"]


def test_new_research_errors_use_safe_codes() -> None:
    from app.errors import (
        AgentExecutionError,
        CitationValidationError,
        ReportGenerationError,
        ResearchExecutionError,
        SearchProviderError,
        VectorStoreError,
    )

    cases = {
        ResearchExecutionError: (500, "research_execution_failed"),
        AgentExecutionError: (500, "agent_execution_failed"),
        SearchProviderError: (503, "search_provider_unavailable"),
        VectorStoreError: (500, "vector_store_failed"),
        CitationValidationError: (500, "citation_validation_failed"),
        ReportGenerationError: (500, "report_generation_failed"),
    }
    for error_type, (status, code) in cases.items():
        error = error_type()
        assert error.status_code == status
        assert error.to_payload("req")["error_code"] == code
        assert error.to_payload("req")["request_id"] == "req"


# ─── Logging fields & redaction ──────────────────────────────────────────────


def test_json_log_contains_required_correlation_fields() -> None:
    record = logging.LogRecord(
        name="app.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="agent.stage.finished",
        args=(),
        exc_info=None,
    )
    record.duration_ms = 42.5
    with log_context(
        request_id="req-1",
        research_id="run-1",
        user_id="user-1",
        agent="analyst",
    ):
        _ContextFilter().filter(record)
        payload = json.loads(JsonFormatter().format(record))

    assert payload["timestamp"].endswith("Z")
    assert payload["level"] == "INFO"
    assert payload["request_id"] == "req-1"
    assert payload["user_id"] == "user-1"
    assert payload["research_id"] == "run-1"
    assert payload["agent_name"] == "analyst"
    assert payload["event"] == "agent.stage.finished"
    assert payload["duration_ms"] == 42.5


@pytest.mark.parametrize(
    "secret",
    [
        "password=hunter2",
        "encryption_key: deadbeefcafe",
        "client_secret=abc123",
        "access_token=ya29.secret-value",
    ],
)
def test_secret_assignments_are_redacted(secret: str) -> None:
    masked = redact(secret)
    assert "***" in masked
    for value in ("hunter2", "deadbeefcafe", "abc123", "ya29.secret-value"):
        assert value not in masked


# ─── CORS ────────────────────────────────────────────────────────────────────


def test_cors_restricts_origins_and_methods(tmp_path: Path) -> None:
    settings = dataclasses.replace(
        get_settings(),
        app_env="test",
        allowed_origins=("http://localhost:3000",),
        log_file=None,
    )
    client = TestClient(create_app(settings))

    allowed = client.options(
        "/health",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:3000"

    foreign = client.options(
        "/health",
        headers={
            "Origin": "http://evil.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert "access-control-allow-origin" not in foreign.headers

    disallowed_method = client.options(
        "/health",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "PUT",
        },
    )
    assert disallowed_method.status_code == 400
