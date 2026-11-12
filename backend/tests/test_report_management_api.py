"""
tests/test_report_management_api.py
===================================
Report metadata, immutable versions, export and ownership.

Reports are seeded through the real ReportManagementService (which writes to
SQLite and ObjectStorage), then exercised through the HTTP API.
"""

from __future__ import annotations

import dataclasses
import queue
import threading
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.factory import create_app
from app.schemas.enums import ReportVersionStatus
from app.schemas.requests import ResearchRunRequest
from app.security.dependencies import get_current_user
from app.services.research_service import ResearchService

CONTENT = "# Findings\n\nArtificial intelligence improves diagnostic accuracy."


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
def user_state() -> dict:
    return {"user_id": "user-a"}


@pytest.fixture
def app(api_settings, user_state):
    application = create_app(api_settings)
    application.dependency_overrides[get_current_user] = lambda: dict(user_state)
    return application


@pytest.fixture
def client(app):
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture
def anonymous_client(api_settings):
    application = create_app(api_settings)
    with TestClient(application, raise_server_exceptions=False) as test_client:
        yield test_client


def _make_research(client: TestClient, name: str = "Reports project") -> str:
    project = client.post("/projects", json={"name": name}).json()
    research = client.post(
        f"/projects/{project['project_id']}/research",
        json={"topic": "Report management topic"},
    ).json()
    return research["research_id"]


def _seed_report(
    app,
    research_id: str,
    content: str = CONTENT,
    *,
    status: ReportVersionStatus = ReportVersionStatus.DRAFT,
    critic_score: int | None = 8,
    title: str = "Test research report",
) -> dict:
    return app.state.container.report_management.create_report(
        "user-a",
        research_id,
        title,
        content,
        critic_score=critic_score,
        status=status,
    )


# ─── Retrieval ──────────────────────────────────────────────────────────────


class TestReportRetrieval:
    def test_list_reports_for_research(self, client, app) -> None:
        research_id = _make_research(client)
        first = _seed_report(app, research_id, "# First\n\nBody one.")
        _seed_report(app, research_id, "# Second\n\nBody two.")

        response = client.get(f"/research/{research_id}/reports")

        assert response.status_code == 200
        body = response.json()
        assert body["research_id"] == research_id
        assert body["count"] == 2
        assert body["pagination"]["total"] == 2
        assert {item["report_id"] for item in body["reports"]} == {
            first["report_id"],
            body["reports"][0]["report_id"],
            body["reports"][1]["report_id"],
        }

    def test_list_filters_by_status_and_paginates(self, client, app) -> None:
        research_id = _make_research(client)
        _seed_report(app, research_id, "# Approved\n\nBody.", status=ReportVersionStatus.APPROVED)
        _seed_report(app, research_id, "# Draft\n\nBody.")

        approved = client.get(
            f"/research/{research_id}/reports", params={"status": "approved"}
        ).json()
        assert approved["count"] == 1
        assert approved["reports"][0]["status"] == "approved"

        page = client.get(
            f"/research/{research_id}/reports",
            params={"limit": 1, "offset": 1, "sort": "created_at", "order": "asc"},
        ).json()
        assert page["pagination"]["total"] == 2
        assert page["count"] == 1
        assert page["pagination"]["has_more"] is False

    def test_get_report_metadata(self, client, app) -> None:
        research_id = _make_research(client)
        seeded = _seed_report(app, research_id)

        response = client.get(f"/reports/{seeded['report_id']}")

        assert response.status_code == 200
        body = response.json()
        assert body["report_id"] == seeded["report_id"]
        assert body["research_id"] == research_id
        assert body["owner_id"] == "user-a"
        assert body["title"] == "Test research report"
        assert body["format"] == "md"
        assert body["version"] == 1
        assert body["status"] == "draft"
        assert body["critic_score"] == 8
        assert body["verification_status"] == "unverified"
        assert body["storage_reference"].startswith("reports/user-a/")
        assert body["created_at"].endswith("Z")

    def test_unknown_and_malformed_reports(self, client) -> None:
        assert client.get(f"/reports/{uuid.uuid4().hex}").status_code == 404
        # Non-ID paths belong to the legacy filename download route.
        assert client.get("/reports/not-a-report-id").status_code == 404
        assert client.get(f"/research/{uuid.uuid4().hex}/reports").status_code == 404

    def test_cross_user_reads_are_404(self, client, app, user_state) -> None:
        research_id = _make_research(client)
        seeded = _seed_report(app, research_id)
        user_state["user_id"] = "user-b"

        assert client.get(f"/research/{research_id}/reports").status_code == 404
        assert client.get(f"/reports/{seeded['report_id']}").status_code == 404
        assert client.get(f"/reports/{seeded['report_id']}/versions").status_code == 404


# ─── Versions ───────────────────────────────────────────────────────────────


class TestReportVersions:
    def test_list_and_get_versions(self, client, app) -> None:
        research_id = _make_research(client)
        seeded = _seed_report(app, research_id)

        versions = client.get(f"/reports/{seeded['report_id']}/versions")
        assert versions.status_code == 200
        assert versions.json()["count"] == 1
        version_id = versions.json()["versions"][0]["version_id"]

        detail = client.get(
            f"/reports/{seeded['report_id']}/versions/{version_id}"
        )
        assert detail.status_code == 200
        assert detail.json()["content_markdown"] == CONTENT
        assert detail.json()["version"] == 1
        assert detail.json()["status"] == "draft"

        unknown = client.get(
            f"/reports/{seeded['report_id']}/versions/{uuid.uuid4().hex}"
        )
        assert unknown.status_code == 404

    def test_new_version_preserves_the_old_body(self, client, app) -> None:
        research_id = _make_research(client)
        seeded = _seed_report(app, research_id)
        v1 = client.get(f"/reports/{seeded['report_id']}/versions").json()["versions"][0]

        response = client.post(
            f"/reports/{seeded['report_id']}/versions",
            json={
                "content_markdown": "# Revised\n\nA second draft.",
                "status": "reviewing",
                "critic_score": 9,
            },
        )

        assert response.status_code == 201, response.text
        assert response.json()["version"] == 2
        assert response.json()["status"] == "reviewing"

        report = client.get(f"/reports/{seeded['report_id']}").json()
        assert report["version"] == 2
        assert report["status"] == "reviewing"
        assert report["critic_score"] == 9

        old = client.get(
            f"/reports/{seeded['report_id']}/versions/{v1['version_id']}"
        ).json()
        assert old["version"] == 1
        assert old["status"] == "draft"
        assert old["content_markdown"] == CONTENT

    def test_new_draft_does_not_overwrite_an_approved_version(self, client, app) -> None:
        research_id = _make_research(client)
        approved_body = "# Approved\n\nThis body was approved."
        seeded = _seed_report(
            app, research_id, approved_body, status=ReportVersionStatus.APPROVED
        )
        v1 = client.get(f"/reports/{seeded['report_id']}/versions").json()["versions"][0]
        assert v1["status"] == "approved"

        created = client.post(
            f"/reports/{seeded['report_id']}/versions",
            json={"content_markdown": "# New draft\n\nUnapproved changes."},
        )
        assert created.status_code == 201

        report = client.get(f"/reports/{seeded['report_id']}").json()
        assert report["version"] == 2
        assert report["status"] == "draft"

        approved = client.get(
            f"/reports/{seeded['report_id']}/versions/{v1['version_id']}"
        ).json()
        assert approved["status"] == "approved"
        assert approved["content_markdown"] == approved_body

    def test_empty_version_is_rejected(self, client, app) -> None:
        research_id = _make_research(client)
        seeded = _seed_report(app, research_id)

        response = client.post(
            f"/reports/{seeded['report_id']}/versions",
            json={"content_markdown": "   "},
        )
        assert response.status_code == 422


# ─── Export ─────────────────────────────────────────────────────────────────


class TestReportExport:
    @pytest.mark.parametrize(
        "fmt,magic",
        [("md", None), ("pdf", b"%PDF"), ("docx", b"PK")],
    )
    def test_export_uses_the_existing_exporter(
        self, client, app, fmt: str, magic: bytes | None
    ) -> None:
        research_id = _make_research(client)
        seeded = _seed_report(app, research_id)

        response = client.post(
            f"/reports/{seeded['report_id']}/export", json={"format": fmt}
        )

        assert response.status_code == 200, response.text
        assert response.headers["content-disposition"].startswith("attachment;")
        if magic is not None:
            assert response.content[: len(magic)] == magic
        else:
            assert "diagnostic accuracy" in response.text

        report = client.get(f"/reports/{seeded['report_id']}").json()
        assert report["format"] == fmt
        assert report["storage_reference"].endswith(f".{fmt}")

    def test_export_specific_old_version(self, client, app) -> None:
        research_id = _make_research(client)
        seeded = _seed_report(app, research_id, "# Version one\n\nOriginal.")
        v1 = client.get(f"/reports/{seeded['report_id']}/versions").json()["versions"][0]
        client.post(
            f"/reports/{seeded['report_id']}/versions",
            json={"content_markdown": "# Version two\n\nNewer."},
        )

        response = client.post(
            f"/reports/{seeded['report_id']}/export",
            json={"format": "md", "version_id": v1["version_id"]},
        )

        assert response.status_code == 200
        assert "Original." in response.text
        # Exporting an old version must not change the current report metadata.
        assert client.get(f"/reports/{seeded['report_id']}").json()["storage_reference"] != ""

    def test_unsupported_export_format_is_422(self, client, app) -> None:
        research_id = _make_research(client)
        seeded = _seed_report(app, research_id)
        response = client.post(
            f"/reports/{seeded['report_id']}/export", json={"format": "txt"}
        )
        assert response.status_code == 422

    def test_cross_user_export_is_404(self, client, app, user_state) -> None:
        research_id = _make_research(client)
        seeded = _seed_report(app, research_id)
        user_state["user_id"] = "user-b"

        response = client.post(
            f"/reports/{seeded['report_id']}/export", json={"format": "pdf"}
        )
        assert response.status_code == 404


# ─── Delete ─────────────────────────────────────────────────────────────────


class TestDeleteReport:
    def test_delete_removes_metadata_versions_and_source_objects(
        self, client, app, api_settings
    ) -> None:
        research_id = _make_research(client)
        seeded = _seed_report(app, research_id)
        source = api_settings.reports_dir / "objects" / seeded["storage_reference"]
        assert source.is_file()

        response = client.delete(f"/reports/{seeded['report_id']}")

        assert response.status_code == 200
        assert response.json() == {
            "status": "deleted",
            "report_id": seeded["report_id"],
        }
        assert not source.exists()
        assert client.get(f"/reports/{seeded['report_id']}").status_code == 404
        assert client.delete(f"/reports/{seeded['report_id']}").status_code == 404

    def test_cross_user_delete_is_404(self, client, app, user_state) -> None:
        research_id = _make_research(client)
        seeded = _seed_report(app, research_id)
        user_state["user_id"] = "user-b"

        assert client.delete(f"/reports/{seeded['report_id']}").status_code == 404
        assert client.get(f"/reports/{seeded['report_id']}").status_code == 404


# ─── Authentication ─────────────────────────────────────────────────────────


class TestReportAuthentication:
    @pytest.mark.parametrize(
        "method,path,kwargs",
        [
            ("get", f"/research/{uuid.uuid4().hex}/reports", {}),
            ("get", f"/reports/{uuid.uuid4().hex}", {}),
            ("get", f"/reports/{uuid.uuid4().hex}/versions", {}),
            ("get", f"/reports/{uuid.uuid4().hex}/versions/{uuid.uuid4().hex}", {}),
            ("post", f"/reports/{uuid.uuid4().hex}/export", {"json": {"format": "pdf"}}),
            (
                "post",
                f"/reports/{uuid.uuid4().hex}/versions",
                {"json": {"content_markdown": "# Draft"}},
            ),
            ("delete", f"/reports/{uuid.uuid4().hex}", {}),
        ],
    )
    def test_endpoints_require_authentication(
        self, anonymous_client: TestClient, method: str, path: str, kwargs: dict
    ) -> None:
        assert getattr(anonymous_client, method)(path, **kwargs).status_code == 401


# ─── Pipeline integration ───────────────────────────────────────────────────


class _Sessions:
    def __init__(self) -> None:
        self.saved: list[dict] = []

    def save_session(self, *args, **kwargs):
        self.saved.append(kwargs)


class _Executor:
    def execute(self, request, user_id=None):
        return {
            "status": "completed",
            "final_report": "# Generated report\n\nReal pipeline output.",
            "critic_score": 9,
            "citations_verified": 2,
            "citations_total": 2,
            "logs": [],
        }


class TestPipelineRegistration:
    def test_completed_run_registers_a_real_report(self, app, api_settings) -> None:
        research_id = uuid.uuid4().hex
        manager = app.state.container.report_management
        service = ResearchService(
            api_settings,
            _Sessions(),  # type: ignore[arg-type]
            _Executor(),  # type: ignore[arg-type]
            reports=manager,
        )

        event_queue: queue.Queue = queue.Queue()
        service.run_blocking(
            request=ResearchRunRequest(topic="Pipeline report topic", provider="groq"),
            user_id="user-a",
            event_queue=event_queue,
            research_id=research_id,
            cancel_event=threading.Event(),
        )

        reports, total = manager.list_reports(research_id, "user-a")
        assert total == 1
        report = reports[0]
        assert report["title"] == "Pipeline report topic"
        assert report["critic_score"] == 9
        assert report["verification_status"] == "verified"

        version = manager.list_versions(report["report_id"], "user-a")[0]
        assert "Real pipeline output." in version["content_markdown"]
