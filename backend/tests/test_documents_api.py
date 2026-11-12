"""
tests/test_documents_api.py
===========================
Document upload/list/status/delete with a fake ObjectStorage and temp SQLite.

Real content validation is exercised (PDF magic, DOCX ZIP structure, UTF-8
text), and uploads are asserted to remain ``uploaded`` — never ``indexed``.
"""

from __future__ import annotations

import dataclasses
import hashlib
import io
import uuid
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.factory import create_app
from app.infrastructure import SQLiteDatabase, StoredObject
from app.repositories.agent_history import AgentHistoryRepository
from app.repositories.documents import DocumentRepository
from app.repositories.projects import ProjectRepository
from app.security.dependencies import get_current_user
from app.services.agent_history_service import AgentHistoryService
from app.services.container import get_document_service
from app.services.document_service import DocumentService
from app.services.project_service import ProjectService


class FakeObjectStorage:
    """In-memory ObjectStorage double; no filesystem writes."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.uploads: list[dict] = []
        self.deleted: list[str] = []

    def upload(self, key, data, *, content_type="application/octet-stream", metadata=None):
        payload = data if isinstance(data, (bytes, bytearray, memoryview)) else data.read()
        self.objects[key] = bytes(payload)
        self.uploads.append(
            {"key": key, "content_type": content_type, "metadata": dict(metadata or {})}
        )
        return StoredObject(
            key=key,
            size=len(payload),
            content_type=content_type,
            metadata=dict(metadata or {}),
        )

    def download(self, key):
        return self.objects[key]

    def delete(self, key):
        self.deleted.append(key)
        return self.objects.pop(key, None) is not None

    def exists(self, key):
        return key in self.objects

    def generate_signed_url(self, key, *, expires_in=3600, method="GET"):
        raise NotImplementedError


def _docx_bytes(text: str = "Hello document") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", f"<w:document>{text}</w:document>")
    return buffer.getvalue()


def _pdf_bytes(text: str = "Hello PDF") -> bytes:
    return b"%PDF-1.4\n1 0 obj\n<<>>\nstream\n" + text.encode() + b"\nendstream\n%%EOF\n"


@pytest.fixture
def api_settings(tmp_path: Path):
    base = get_settings()
    return dataclasses.replace(
        base,
        reports_dir=tmp_path / "reports",
        exports_dir=tmp_path / "reports" / "exports",
        sessions_db_path=tmp_path / "sessions.db",
        max_document_bytes=1024 * 1024,
        log_file=None,
    )


@pytest.fixture
def storage() -> FakeObjectStorage:
    return FakeObjectStorage()


@pytest.fixture
def user_state() -> dict:
    return {"user_id": "user-a"}


def _build_client(settings, storage, user_state) -> TestClient:
    database = SQLiteDatabase(settings.sessions_db_path)
    history = AgentHistoryService(AgentHistoryRepository(database))
    projects = ProjectService(ProjectRepository(database), history)
    service = DocumentService(
        DocumentRepository(database),
        storage,
        projects,
        history,
        max_bytes=settings.max_document_bytes,
    )

    application = create_app(settings)
    application.dependency_overrides[get_current_user] = lambda: dict(user_state)
    application.dependency_overrides[get_document_service] = lambda: service
    test_client = TestClient(application, raise_server_exceptions=False)
    test_client.user_state = user_state  # type: ignore[attr-defined]
    test_client.storage = storage  # type: ignore[attr-defined]
    return test_client


@pytest.fixture
def client(api_settings, storage, user_state):
    with _build_client(api_settings, storage, user_state) as test_client:
        yield test_client


@pytest.fixture
def anonymous_client(api_settings, storage):
    application = create_app(api_settings)
    with TestClient(application, raise_server_exceptions=False) as test_client:
        yield test_client


def _upload(client: TestClient, name: str, data: bytes, content_type: str, **form):
    return client.post(
        "/documents/upload",
        files={"file": (name, data, content_type)},
        data=form,
    )


# ─── Successful uploads ─────────────────────────────────────────────────────


class TestUpload:
    def test_upload_pdf_returns_metadata_and_stores_bytes(
        self, client: TestClient, storage: FakeObjectStorage
    ) -> None:
        payload = _pdf_bytes()
        response = _upload(client, "study.pdf", payload, "application/pdf")

        assert response.status_code == 201, response.text
        body = response.json()
        assert len(body["document_id"]) == 32
        assert body["owner_id"] == "user-a"
        assert body["original_filename"] == "study.pdf"
        assert body["mime_type"] == "application/pdf"
        assert body["size_bytes"] == len(payload)
        assert body["sha256"] == hashlib.sha256(payload).hexdigest()
        assert body["storage_key"].startswith("documents/user-a/")
        assert body["processing_status"] == "uploaded"
        assert body["error_code"] is None
        assert storage.objects[body["storage_key"]] == payload

    @pytest.mark.parametrize(
        "name,data,mime",
        [
            ("notes.txt", b"Plain text research notes.", "text/plain"),
            ("notes.md", b"# Markdown notes\n\nFindings.", "text/markdown"),
            ("paper.docx", _docx_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ],
    )
    def test_supported_formats(
        self, client: TestClient, name: str, data: bytes, mime: str
    ) -> None:
        response = _upload(client, name, data, mime)
        assert response.status_code == 201, response.text
        assert response.json()["processing_status"] == "uploaded"

    def test_generic_octet_stream_is_inferred_from_extension(
        self, client: TestClient
    ) -> None:
        response = _upload(client, "notes.txt", b"Some notes", "application/octet-stream")
        assert response.status_code == 201
        assert response.json()["mime_type"] == "text/plain"

    def test_filename_is_reduced_to_a_safe_basename(
        self, client: TestClient
    ) -> None:
        response = _upload(client, "../../etc/evil.txt", b"payload", "text/plain")

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["original_filename"] == "evil.txt"
        assert ".." not in body["storage_key"]

    def test_upload_never_claims_indexed(self, client: TestClient) -> None:
        body = _upload(client, "notes.txt", b"content", "text/plain").json()
        assert body["processing_status"] == "uploaded"
        assert body["processing_status"] != "indexed"


# ─── Validation ─────────────────────────────────────────────────────────────


class TestValidation:
    def test_empty_file_is_rejected(self, client: TestClient) -> None:
        response = _upload(client, "empty.txt", b"", "text/plain")
        assert response.status_code == 400

    def test_unsupported_extension_is_rejected(self, client: TestClient) -> None:
        response = _upload(client, "payload.exe", b"MZ...", "application/octet-stream")
        assert response.status_code == 400
        assert "Unsupported" in response.json()["detail"]

    def test_extension_mime_mismatch_is_rejected(self, client: TestClient) -> None:
        response = _upload(client, "study.pdf", _pdf_bytes(), "text/plain")
        assert response.status_code == 400

    def test_pdf_with_text_content_is_rejected(self, client: TestClient) -> None:
        response = _upload(client, "fake.pdf", b"this is not a pdf", "application/pdf")
        assert response.status_code == 400
        assert "valid PDF" in response.json()["detail"]

    def test_docx_that_is_not_a_word_package_is_rejected(
        self, client: TestClient
    ) -> None:
        response = _upload(
            client,
            "fake.docx",
            b"not a zip",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        assert response.status_code == 400

    def test_non_utf8_text_is_rejected(self, client: TestClient) -> None:
        response = _upload(client, "bad.txt", b"\xff\xfe\x00bad", "text/plain")
        assert response.status_code == 400

    def test_size_limit_is_enforced(self, api_settings, storage, user_state) -> None:
        settings = dataclasses.replace(api_settings, max_document_bytes=32)
        with _build_client(settings, storage, user_state) as small_client:
            response = _upload(small_client, "big.txt", b"x" * 33, "text/plain")
        assert response.status_code == 400
        assert "limit" in response.json()["detail"]

    def test_duplicate_content_is_a_conflict(
        self, client: TestClient, storage: FakeObjectStorage
    ) -> None:
        payload = b"identical content"
        first = _upload(client, "one.txt", payload, "text/plain")
        second = _upload(client, "two.txt", payload, "text/plain")

        assert first.status_code == 201
        assert second.status_code == 409
        assert second.json()["details"]["document_id"] == first.json()["document_id"]
        assert len(storage.objects) == 1

    def test_malformed_project_id_is_422(self, client: TestClient) -> None:
        response = _upload(
            client,
            "notes.txt",
            b"content",
            "text/plain",
            project_id="not-a-uuid",
        )
        assert response.status_code == 422


# ─── Project / research ownership ───────────────────────────────────────────


class TestLinking:
    def test_upload_to_owned_project(self, client: TestClient) -> None:
        project = client.post("/projects", json={"name": "Docs"}).json()
        response = _upload(
            client,
            "notes.txt",
            b"project content",
            "text/plain",
            project_id=project["project_id"],
        )

        assert response.status_code == 201
        assert response.json()["project_id"] == project["project_id"]

    def test_upload_to_foreign_project_is_404(
        self, client: TestClient, user_state
    ) -> None:
        project = client.post("/projects", json={"name": "Mine"}).json()
        user_state["user_id"] = "user-b"

        response = _upload(
            client,
            "notes.txt",
            b"foreign content",
            "text/plain",
            project_id=project["project_id"],
        )
        assert response.status_code == 404

    def test_upload_to_owned_research(self, client: TestClient) -> None:
        project = client.post("/projects", json={"name": "Research"}).json()
        research = client.post(
            f"/projects/{project['project_id']}/research", json={"topic": "Doc topic"}
        ).json()

        response = _upload(
            client,
            "notes.txt",
            b"research content",
            "text/plain",
            project_id=project["project_id"],
            research_id=research["research_id"],
        )
        assert response.status_code == 201
        assert response.json()["research_id"] == research["research_id"]

    def test_upload_to_unknown_research_is_404(self, client: TestClient) -> None:
        response = _upload(
            client,
            "notes.txt",
            b"unknown research",
            "text/plain",
            research_id=uuid.uuid4().hex,
        )
        assert response.status_code == 404


# ─── Listing and retrieval ──────────────────────────────────────────────────


class TestListingAndRetrieval:
    def test_list_filters_and_paginates(self, client: TestClient) -> None:
        for name, content in (
            ("alpha.txt", b"alpha content"),
            ("beta.md", b"# beta"),
            ("gamma.txt", b"gamma content"),
        ):
            assert _upload(client, name, content, "text/plain").status_code == 201

        page = client.get(
            "/documents", params={"limit": 2, "offset": 1, "sort": "original_filename", "order": "asc"}
        ).json()
        assert page["pagination"]["total"] == 3
        assert page["count"] == 2
        assert [item["original_filename"] for item in page["documents"]] == [
            "beta.md",
            "gamma.txt",
        ]

        search = client.get("/documents", params={"q": "gamma"}).json()
        assert search["count"] == 1

        status_filter = client.get("/documents", params={"status": "uploaded"}).json()
        assert status_filter["count"] == 3

    def test_get_document_and_status(self, client: TestClient) -> None:
        created = _upload(client, "notes.txt", b"get me", "text/plain").json()

        detail = client.get(f"/documents/{created['document_id']}")
        status = client.get(f"/documents/{created['document_id']}/status")

        assert detail.status_code == 200
        assert detail.json()["document_id"] == created["document_id"]
        assert status.status_code == 200
        assert status.json()["processing_status"] == "uploaded"
        assert status.json()["error_code"] is None

    def test_cross_user_access_is_hidden(self, client: TestClient, user_state) -> None:
        created = _upload(client, "notes.txt", b"private", "text/plain").json()
        user_state["user_id"] = "user-b"

        assert client.get(f"/documents/{created['document_id']}").status_code == 404
        assert (
            client.get(f"/documents/{created['document_id']}/status").status_code == 404
        )
        assert client.get("/documents").json()["count"] == 0

    def test_malformed_document_id_is_422(self, client: TestClient) -> None:
        assert client.get("/documents/not-a-document-id").status_code == 422
        assert client.get("/documents/not-a-document-id/status").status_code == 422


# ─── Deletion ───────────────────────────────────────────────────────────────


class TestDeletion:
    def test_delete_removes_metadata_and_stored_bytes(
        self, client: TestClient, storage: FakeObjectStorage
    ) -> None:
        created = _upload(client, "notes.txt", b"delete me", "text/plain").json()
        key = created["storage_key"]

        response = client.delete(f"/documents/{created['document_id']}")

        assert response.status_code == 200
        assert response.json() == {
            "status": "deleted",
            "document_id": created["document_id"],
        }
        assert key in storage.deleted
        assert key not in storage.objects
        assert client.get(f"/documents/{created['document_id']}").status_code == 404

    def test_cross_user_delete_is_404(
        self, client: TestClient, user_state, storage: FakeObjectStorage
    ) -> None:
        created = _upload(client, "notes.txt", b"owned", "text/plain").json()
        user_state["user_id"] = "user-b"

        assert client.delete(f"/documents/{created['document_id']}").status_code == 404
        assert created["storage_key"] in storage.objects


# ─── Authentication ─────────────────────────────────────────────────────────


class TestAuthentication:
    @pytest.mark.parametrize(
        "method,path,kwargs",
        [
            ("get", "/documents", {}),
            ("get", f"/documents/{uuid.uuid4().hex}", {}),
            ("get", f"/documents/{uuid.uuid4().hex}/status", {}),
            ("delete", f"/documents/{uuid.uuid4().hex}", {}),
        ],
    )
    def test_read_endpoints_require_auth(
        self, anonymous_client: TestClient, method: str, path: str, kwargs: dict
    ) -> None:
        assert getattr(anonymous_client, method)(path, **kwargs).status_code == 401

    def test_upload_requires_auth(self, anonymous_client: TestClient) -> None:
        response = anonymous_client.post(
            "/documents/upload",
            files={"file": ("notes.txt", b"content", "text/plain")},
        )
        assert response.status_code == 401
