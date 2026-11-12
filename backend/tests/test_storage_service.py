"""
tests/test_storage_service.py
==============================
Unit tests for the secure local storage service.

Exercises user/project-scoped safe paths, validation and size limits,
ownership-scoped authorization, report versioning, deletion, and
backup/restore — all against a fake ``ObjectStorage`` and a temp SQLite DB.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from app.errors import (
    AuthenticationError,
    BadRequestError,
    ConflictError,
    FileProcessingError,
    NotFoundError,
)
from app.infrastructure import (
    LocalObjectStorage,
    SQLiteDatabase,
    StoredObject,
)
from app.repositories.documents import DocumentRepository
from app.repositories.reports import ReportRepository
from app.schemas.enums import ReportVersionStatus
from app.services.storage_service import SecureLocalStorageService, StorageService


class FakeObjectStorage:
    """In-memory ObjectStorage double; no filesystem writes."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.uploads: list[dict] = []
        self.deleted: list[str] = []
        self._backups: list[dict[str, bytes]] = []

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

    def backup(self) -> str:
        self._backups.append(dict(self.objects))
        return f"backup-{len(self._backups)}"

    def restore(self, backup_id: str) -> int:
        idx = int(backup_id.rsplit("-", 1)[1]) - 1
        snapshot = self._backups[idx]
        self.objects.update(snapshot)
        return len(snapshot)


def _pdf_bytes(text: str = "Hello PDF") -> bytes:
    return b"%PDF-1.4\n1 0 obj\n<<>>\nstream\n" + text.encode() + b"\nendstream\n%%EOF\n"


def _docx_bytes(text: str = "Hello document") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", f"<w:document>{text}</w:document>")
    return buffer.getvalue()


@pytest.fixture
def database(tmp_path: Path):
    db = SQLiteDatabase(tmp_path / "sessions.db")
    return db


@pytest.fixture
def storage():
    return FakeObjectStorage()


@pytest.fixture
def svc(storage, database):
    return SecureLocalStorageService(
        storage=storage,
        documents=DocumentRepository(database),
        reports=ReportRepository(database),
        projects=None,
        max_document_bytes=1024 * 1024,
    )


def test_implements_storage_service_protocol(svc):
    assert isinstance(svc, StorageService)


def test_upload_document_user_project_scoped_key(svc, storage):
    row = svc.upload_document(
        owner_id="user-1",
        filename="notes.pdf",
        content_type="application/pdf",
        data=_pdf_bytes(),
        project_id="proj-7",
    )
    assert row["owner_id"] == "user-1"
    assert row["project_id"] == "proj-7"
    assert row["original_filename"] == "notes.pdf"
    key = storage.uploads[-1]["key"]
    assert key.startswith("documents/user-1/proj-7/")
    assert storage.objects[key]


def test_upload_rejects_oversized_file(svc):
    big = _pdf_bytes("x" * (1024 * 1024 + 1))
    with pytest.raises(BadRequestError):
        svc.upload_document(
            owner_id="u1", filename="big.pdf", content_type="application/pdf", data=big
        )


def test_upload_sanitizes_unsafe_filename(svc):
    # safe_filename() strips traversal and confines the name to a basename
    # rather than raising; the resulting key must stay under the root.
    row = svc.upload_document(
        owner_id="u1",
        filename="../../etc/passwd.pdf",
        content_type="application/pdf",
        data=_pdf_bytes(),
    )
    assert row["original_filename"] == "passwd.pdf"
    assert ".." not in row["storage_key"]
    assert row["storage_key"].startswith("documents/u1/")


def test_upload_rejects_invalid_content(svc):
    with pytest.raises(BadRequestError):
        svc.upload_document(
            owner_id="u1",
            filename="fake.pdf",
            content_type="application/pdf",
            data=b"definitely not a pdf",
        )


def test_upload_detects_duplicate_content(svc):
    svc.upload_document(
        owner_id="u1", filename="a.pdf", content_type="application/pdf", data=_pdf_bytes("same")
    )
    with pytest.raises(ConflictError):
        svc.upload_document(
            owner_id="u1",
            filename="b.pdf",
            content_type="application/pdf",
            data=_pdf_bytes("same"),
        )


def test_download_document_requires_owner(svc):
    svc.upload_document(
        owner_id="u1", filename="a.pdf", content_type="application/pdf", data=_pdf_bytes()
    )
    with pytest.raises(NotFoundError):
        svc.download_document("does-not-exist", "u1")


def test_download_document_returns_bytes(svc, storage):
    row = svc.upload_document(
        owner_id="u1", filename="a.pdf", content_type="application/pdf", data=_pdf_bytes()
    )
    payload, meta = svc.download_document(row["document_id"], "u1")
    assert payload == storage.objects[row["storage_key"]]
    assert meta["document_id"] == row["document_id"]


def test_delete_document_removes_bytes_and_metadata(svc, storage):
    row = svc.upload_document(
        owner_id="u1", filename="a.pdf", content_type="application/pdf", data=_pdf_bytes()
    )
    svc.delete_document(row["document_id"], "u1")
    assert row["storage_key"] in storage.deleted
    assert row["storage_key"] not in storage.objects
    with pytest.raises(NotFoundError):
        svc.get_document(row["document_id"], "u1")


def test_report_versioning_and_delete(svc, storage):
    report = svc.create_report(
        owner_id="u1",
        research_id="res-1",
        title="Vacuum decay",
        content_markdown="# Vacuum decay\n\nTL;DR.",
    )
    assert report["version"] == 1
    v2 = svc.add_report_version(
        report["report_id"],
        "u1",
        "# Vacuum decay v2\n\nMore.",
        change_summary="expanded",
    )
    assert v2["version"] == 2

    versions = svc.list_report_versions(report["report_id"], "u1")
    assert [v["version"] for v in versions] == [2, 1]

    payload, meta = svc.download_report_version(
        report["report_id"], v2["version_id"], "u1"
    )
    assert payload == b"# Vacuum decay v2\n\nMore."

    # Delete it; all version bytes must be gone.
    svc.delete_report(report["report_id"], "u1")
    for v in versions:
        ref = v.get("storage_reference")
        if ref:
            assert ref in storage.deleted
            assert ref not in storage.objects
    with pytest.raises(NotFoundError):
        svc.get_report(report["report_id"], "u1")


def test_access_is_ownership_scoped(svc):
    report = svc.create_report(
        owner_id="u1",
        research_id="res-1",
        title="Mine",
        content_markdown="# Mine",
    )
    with pytest.raises(NotFoundError):
        svc.get_report(report["report_id"], "u2")
    with pytest.raises(NotFoundError):
        svc.add_report_version(report["report_id"], "u2", "# not yours")


def test_requires_owner(svc):
    with pytest.raises(AuthenticationError):
        svc.upload_document(owner_id="", filename="a.pdf", content_type="application/pdf", data=_pdf_bytes())


def test_backup_then_restore(tmp_path: Path):
    storage = LocalObjectStorage(tmp_path / "objects")
    svc = SecureLocalStorageService(
        storage=storage,
        documents=DocumentRepository(SQLiteDatabase(tmp_path / "sessions.db")),
        reports=ReportRepository(SQLiteDatabase(tmp_path / "sessions.db")),
        projects=None,
    )
    row = svc.upload_document(
        owner_id="u1", filename="a.pdf", content_type="application/pdf", data=_pdf_bytes()
    )
    backup_id = svc.backup()
    # Destroy the live file.
    storage.delete(row["storage_key"])
    assert not storage.exists(row["storage_key"])
    restored = svc.restore(backup_id)
    assert restored >= 1
    assert storage.exists(row["storage_key"])


def test_backup_rejects_unbacked_backend(svc):
    # FakeObjectStorage implements backup/restore; drop them.
    svc._storage.backup = None  # type: ignore[assignment]
    with pytest.raises(FileProcessingError):
        svc.backup()
