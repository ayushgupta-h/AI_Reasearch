"""
tests/test_services.py
======================
Service layer: database error translation, report path policy, export naming.
"""

from __future__ import annotations

import dataclasses
import sqlite3
from pathlib import Path

import pytest

from app.config import get_settings
from app.errors import AuthorizationError, BadRequestError, DatabaseError, NotFoundError
from app.schemas.requests import ExportRequest
from app.services.report_service import ReportService
from app.services.session_service import SessionService


class _BrokenManager:
    """Mimics a locked/corrupt SQLite database."""

    def get_all_sessions(self, user_id=None):
        raise sqlite3.OperationalError("database is locked")

    def get_session(self, session_id, user_id=None):
        raise sqlite3.OperationalError("database is locked")

    def delete_session(self, session_id, user_id=None):
        raise sqlite3.OperationalError("database is locked")

    def save_session(self, *args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    def user_owns_report(self, user_id, filename):
        raise sqlite3.OperationalError("database is locked")


class TestSessionServiceErrors:
    @pytest.fixture
    def service(self) -> SessionService:
        return SessionService(_BrokenManager())  # type: ignore[arg-type]

    def test_list_sessions_translates_sqlite_error(self, service: SessionService) -> None:
        with pytest.raises(DatabaseError):
            service.list_sessions()

    def test_delete_session_translates_sqlite_error(self, service: SessionService) -> None:
        with pytest.raises(DatabaseError):
            service.delete_session("id", user_id="u1")

    def test_save_session_translates_sqlite_error(self, service: SessionService) -> None:
        with pytest.raises(DatabaseError):
            service.save_session({}, duration_seconds=1.0, user_id="u1")

    def test_ownership_check_translates_sqlite_error(self, service: SessionService) -> None:
        with pytest.raises(DatabaseError):
            service.user_owns_report("u1", "report.md")


class _AllowAllSessions:
    def user_owns_report(self, user_id, filename):
        return True


@pytest.fixture
def report_settings(tmp_path: Path):
    settings = get_settings()
    reports = tmp_path / "reports"
    return dataclasses.replace(
        settings, reports_dir=reports, exports_dir=reports / "exports"
    )


@pytest.fixture
def report_service(report_settings) -> ReportService:
    report_settings.ensure_directories()
    return ReportService(report_settings, _AllowAllSessions())  # type: ignore[arg-type]


class TestReportPathPolicy:
    def test_missing_report_raises_not_found(self, report_service: ReportService) -> None:
        with pytest.raises(NotFoundError):
            report_service.resolve("nope.md")

    def test_traversal_is_rejected(
        self, report_service: ReportService, report_settings
    ) -> None:
        outside = report_settings.reports_dir.parent / "secret.md"
        outside.write_text("secret", encoding="utf-8")
        # Traversal segments are rejected outright, never flattened into a hit.
        with pytest.raises(AuthorizationError):
            report_service.resolve("../../secret.md")

    def test_media_type_mapping(self, report_service: ReportService, report_settings) -> None:
        (report_settings.reports_dir / "r.pdf").write_bytes(b"%PDF-1.4")
        assert report_service.media_type_for(report_settings.reports_dir / "r.pdf") == "application/pdf"
        assert report_service.media_type_for(report_settings.reports_dir / "r.md").startswith("text/markdown")

    def test_share_token_generated_for_existing_report(
        self, report_service: ReportService, report_settings
    ) -> None:
        (report_settings.reports_dir / "r.md").write_text("# Report", encoding="utf-8")
        link = report_service.share_link("r.md", user={"user_id": "user-1"})
        assert link["filename"] == "r.md"
        assert len(link["share_token"]) == 24
        assert "run=r.md" in link["share_url"]

    def test_download_requires_auth_without_share_token(
        self, report_service: ReportService, report_settings
    ) -> None:
        (report_settings.reports_dir / "r.md").write_text("# Report", encoding="utf-8")
        with pytest.raises(Exception) as excinfo:
            report_service.authorize_download("r.md", user=None, share_token=None)
        assert "Authentication required" in str(excinfo.value)

    def test_valid_share_token_grants_read_only_access(
        self, report_service: ReportService, report_settings
    ) -> None:
        (report_settings.reports_dir / "r.md").write_text("# Report", encoding="utf-8")
        token = report_service.share_link("r.md", user={"user_id": "user-1"})["share_token"]
        _, media_type, read_only = report_service.authorize_download(
            "r.md", user=None, share_token=token
        )
        assert read_only is True
        assert media_type.startswith("text/markdown")


class TestExportPolicy:
    def test_export_requires_content(self, report_service: ReportService) -> None:
        request = ExportRequest(content="   ", format="md")
        with pytest.raises(BadRequestError):
            report_service.export(request)

    def test_markdown_export_creates_file(
        self, report_service: ReportService, report_settings
    ) -> None:
        request = ExportRequest(content="# Findings\n\nText.", topic="AI in Health Care", format="md")
        path, filename, media_type = report_service.export(request)
        assert path.is_file()
        assert filename.startswith("quest_ai_in_health_care_")
        assert filename.endswith(".md")
        assert media_type.startswith("text/markdown")
