"""
app/services/storage_service.py
================================
Secure local storage service for uploaded documents and generated reports.

This module defines the reusable :class:`StorageService` interface and a
concrete :class:`SecureLocalStorageService`. The interface is the seam where a
cloud-backed implementation (S3, GCS, MinIO, Supabase Storage) can later be
substituted without changing routes or services.

Security and layout guarantees implemented here:

* **User- and project-scoped directories** — object keys are namespaced as
  ``documents/{owner}/{project_id|_}/{document_id}/{safe_name}`` and
  ``reports/{owner}/{report_id}/v{version}.md``.
* **Safe file paths** — keys are normalised and confined to the storage root by
  :class:`app.infrastructure.local_storage.LocalObjectStorage` (no absolute
  paths, no ``..`` traversal, no null bytes).
* **File validation** — extension allowlist, declared MIME agreement, and
  content sniffing (PDF magic bytes / DOCX ZIP package / UTF-8 text).
* **File size limits** — enforced before anything touches disk.
* **Metadata in the application database** — via ``DocumentRepository`` and
  ``ReportRepository``; bytes live behind the ``ObjectStorage`` port.
* **Secure download authorization** — every read is scoped by ``owner_id``; the
  frontend never supplies the owner, the validated JWT does.
* **Report versioning** — each version is a new immutable row plus a stored
  object; deleting a report removes every version's bytes.
* **File deletion** — bytes are removed together with their metadata.
* **Backup & recovery** — delegates to the object store's snapshot/restore.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Tuple, runtime_checkable, runtime_checkable

from app.errors import (
    AuthenticationError,
    BadRequestError,
    ConflictError,
    FileProcessingError,
    NotFoundError,
)
from app.infrastructure.ports import ObjectStorage
from app.observability import get_logger
from app.repositories.documents import DocumentRepository
from app.repositories.reports import ReportRepository
from app.schemas.enums import (
    ReportVerificationStatus,
    ReportVersionStatus,
)
from app.services.document_service import (
    ALLOWED_EXTENSIONS,
    CANONICAL_MIME,
    _validate_content,
    safe_filename,
)
from app.services.project_service import ProjectService

logger = get_logger("app.services.storage")


@runtime_checkable
class StorageService(Protocol):
    """Reusable storage boundary for documents and report versions.

    Implementations may be local (filesystem + application DB) or cloud-backed;
    the application code depends only on this interface.
    """

    # ── Documents ────────────────────────────────────────────────────────

    def upload_document(
        self,
        *,
        owner_id: str,
        filename: str,
        content_type: str,
        data: bytes,
        project_id: Optional[str] = None,
        research_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Validate, store bytes and record metadata. Returns the document row."""

    def download_document(self, document_id: str, owner_id: str) -> Tuple[bytes, Dict[str, Any]]:
        """Return (bytes, metadata row) or raise ``NotFoundError``."""

    def get_document(self, document_id: str, owner_id: str) -> Dict[str, Any]:
        """Return document metadata only."""

    def list_documents(
        self,
        owner_id: str,
        *,
        project_id: Optional[str] = None,
        research_id: Optional[str] = None,
        status: Optional[Any] = None,
        q: Optional[str] = None,
        sort: str = "created_at",
        order: str = "desc",
        limit: int = 20,
        offset: int = 0,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Return (rows, total) for the caller's documents."""

    def delete_document(self, document_id: str, owner_id: str) -> None:
        """Delete bytes and metadata for an owned document."""

    # ── Reports / versions ────────────────────────────────────────────────

    def create_report(
        self,
        *,
        owner_id: str,
        research_id: str,
        title: str,
        content_markdown: str,
        critic_score: Optional[int] = None,
        verification_status: ReportVerificationStatus = ReportVerificationStatus.UNVERIFIED,
        status: ReportVersionStatus = ReportVersionStatus.DRAFT,
    ) -> Dict[str, Any]:
        """Create a report plus its first version (v1)."""

    def add_report_version(
        self,
        report_id: str,
        owner_id: str,
        content_markdown: str,
        *,
        status: ReportVersionStatus = ReportVersionStatus.DRAFT,
        critic_score: Optional[int] = None,
        verification_status: ReportVerificationStatus = ReportVerificationStatus.UNVERIFIED,
        change_summary: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create the next immutable version for an owned report."""

    def get_report(self, report_id: str, owner_id: str) -> Dict[str, Any]:
        """Return report metadata or raise ``NotFoundError``."""

    def list_report_versions(
        self, report_id: str, owner_id: str
    ) -> List[Dict[str, Any]]:
        """Return versions newest-first (raises ``NotFoundError`` if not owned)."""

    def download_report_version(
        self, report_id: str, version_id: str, owner_id: str
    ) -> Tuple[bytes, Dict[str, Any]]:
        """Return (bytes, version row) for an owned report version."""

    def delete_report(self, report_id: str, owner_id: str) -> None:
        """Delete a report, every version's bytes, and all metadata."""

    # ── Backup / recovery ─────────────────────────────────────────────────

    def backup(self) -> str:
        """Snapshot all stored objects. Returns a backup identifier/location."""

    def restore(self, backup_id: str) -> int:
        """Restore objects from a previous snapshot. Returns files restored."""


class SecureLocalStorageService:
    """Filesystem-backed, authorization-scoped implementation of ``StorageService``."""

    def __init__(
        self,
        *,
        storage: ObjectStorage,
        documents: DocumentRepository,
        reports: ReportRepository,
        projects: Optional[ProjectService] = None,
        max_document_bytes: int = 20 * 1024 * 1024,
    ) -> None:
        self._storage = storage
        self._documents = documents
        self._reports = reports
        self._projects = projects
        self._max_bytes = max_document_bytes

    # ── Document helpers ──────────────────────────────────────────────────

    @staticmethod
    def _require_owner(owner_id: Optional[str]) -> str:
        owner = (owner_id or "").strip()
        if not owner:
            raise AuthenticationError("Authentication required.")
        return owner

    def _require_project(self, owner: str, project_id: Optional[str]) -> None:
        if project_id is None:
            return
        if self._projects is None:
            return
        # get_project enforces ownership and raises NotFoundError otherwise.
        self._projects.get_project(project_id, owner)

    def _require_research(
        self, owner: str, research_id: Optional[str], project_id: Optional[str]
    ) -> None:
        if research_id is None:
            return
        if self._projects is not None:
            self._projects.get_research(research_id, owner)

    def _document_key(self, owner: str, project_id: Optional[str], document_id: str, name: str) -> str:
        project = project_id or "_"
        return f"documents/{owner}/{project}/{document_id}/{name}"

    # ── Documents ────────────────────────────────────────────────────────

    def upload_document(
        self,
        *,
        owner_id: str,
        filename: str,
        content_type: str,
        data: bytes,
        project_id: Optional[str] = None,
        research_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        owner = self._require_owner(owner_id)
        name = safe_filename(filename)
        payload = bytes(data or b"")

        if not payload:
            raise BadRequestError("The uploaded file is empty.")
        if len(payload) > self._max_bytes:
            raise BadRequestError(
                f"File exceeds the {self._max_bytes}-byte upload limit."
            )

        extension = Path(name).suffix.lower()
        if extension not in ALLOWED_EXTENSIONS:
            raise BadRequestError(
                "Unsupported document type. Allowed formats: PDF, DOCX, TXT, Markdown."
            )
        declared = (content_type or "").split(";", 1)[0].strip().lower()
        from app.services.document_service import ALLOWED_DECLARED_MIME

        if (
            declared
            and declared != "application/octet-stream"
            and declared not in ALLOWED_DECLARED_MIME[extension]
        ):
            raise BadRequestError("The declared content type does not match the file extension.")
        _validate_content(extension, payload)

        import hashlib

        digest = hashlib.sha256(payload).hexdigest()
        duplicate = self._documents.find_by_hash(owner, digest)
        if duplicate is not None:
            raise ConflictError(
                "A document with identical content already exists.",
                details={"document_id": duplicate["document_id"]},
            )

        self._require_project(owner, project_id)
        self._require_research(owner, research_id, project_id)

        document_id = uuid.uuid4().hex
        storage_key = self._document_key(owner, project_id, document_id, name)
        try:
            self._storage.upload(
                storage_key,
                payload,
                content_type=CANONICAL_MIME[extension],
                metadata={
                    "owner_id": owner,
                    "sha256": digest,
                    "original_filename": name,
                    "project_id": project_id or "",
                },
            )
        except Exception as exc:
            logger.exception("storage.document_upload_failed")
            raise FileProcessingError("Could not store the uploaded document.") from exc

        try:
            row = self._documents.create(
                owner_id=owner,
                project_id=project_id,
                research_id=research_id,
                original_filename=name,
                mime_type=CANONICAL_MIME[extension],
                size_bytes=len(payload),
                sha256=digest,
                storage_key=storage_key,
                processing_status="uploaded",
            )
        except Exception:
            try:
                self._storage.delete(storage_key)
            except Exception:  # pragma: no cover - best-effort cleanup
                logger.exception("storage.document_cleanup_failed")
            raise

        logger.info("storage.document_uploaded", extra={"document_id": document_id})
        return row

    def get_document(self, document_id: str, owner_id: str) -> Dict[str, Any]:
        owner = self._require_owner(owner_id)
        row = self._documents.get(document_id, owner)
        if row is None:
            raise NotFoundError("Document not found.")
        return row

    def download_document(self, document_id: str, owner_id: str) -> Tuple[bytes, Dict[str, Any]]:
        row = self.get_document(document_id, owner_id)
        key = row["storage_key"]
        try:
            payload = self._storage.download(key)
        except FileNotFoundError as exc:
            logger.warning("storage.document_missing_on_disk", extra={"key": key})
            raise NotFoundError("Document file is missing.") from exc
        return payload, row

    def list_documents(
        self,
        owner_id: str,
        *,
        project_id: Optional[str] = None,
        research_id: Optional[str] = None,
        status: Optional[Any] = None,
        q: Optional[str] = None,
        sort: str = "created_at",
        order: str = "desc",
        limit: int = 20,
        offset: int = 0,
    ) -> Tuple[List[Dict[str, Any]], int]:
        owner = self._require_owner(owner_id)
        return self._documents.list_documents(
            owner_id=owner,
            project_id=project_id,
            research_id=research_id,
            status=status,
            q=q,
            sort=sort,
            order=order,
            limit=limit,
            offset=offset,
        )

    def delete_document(self, document_id: str, owner_id: str) -> None:
        row = self.get_document(document_id, owner_id)
        try:
            self._storage.delete(row["storage_key"])
        except Exception as exc:
            logger.exception("storage.document_delete_failed")
            raise FileProcessingError("Could not delete the stored document.") from exc
        self._documents.delete(document_id, row["owner_id"])
        logger.info("storage.document_deleted", extra={"document_id": document_id})

    # ── Report helpers ────────────────────────────────────────────────────

    @staticmethod
    def _report_key(owner: str, report_id: str, version: int) -> str:
        return f"reports/{owner}/{report_id}/v{version}.md"

    def _require_report(self, report_id: str, owner: str) -> Dict[str, Any]:
        row = self._reports.get_report(report_id, owner)
        if row is None:
            raise NotFoundError("Report not found.")
        return row

    def _store_report_bytes(
        self, key: str, content: str, owner: str, report_id: str, version: int
    ) -> None:
        try:
            self._storage.upload(
                key,
                content.encode("utf-8"),
                content_type="text/markdown; charset=utf-8",
                metadata={
                    "owner_id": owner,
                    "report_id": report_id,
                    "version": str(version),
                },
            )
        except Exception as exc:
            logger.exception("storage.report_store_failed")
            raise FileProcessingError("Could not store the report content.") from exc

    def _safe_delete(self, key: str) -> None:
        try:
            self._storage.delete(key)
        except Exception:  # pragma: no cover - best-effort
            logger.exception("storage.report_cleanup_failed")

    # ── Reports / versions ────────────────────────────────────────────────

    def create_report(
        self,
        *,
        owner_id: str,
        research_id: str,
        title: str,
        content_markdown: str,
        critic_score: Optional[int] = None,
        verification_status: ReportVerificationStatus = ReportVerificationStatus.UNVERIFIED,
        status: ReportVersionStatus = ReportVersionStatus.DRAFT,
    ) -> Dict[str, Any]:
        owner = self._require_owner(owner_id)
        clean_title = (title or "").strip()
        content = content_markdown or ""
        if not (3 <= len(clean_title) <= 200):
            raise BadRequestError("Report title must be between 3 and 200 characters.")
        if not content.strip():
            raise BadRequestError("Cannot create an empty report.")

        report_id = uuid.uuid4().hex
        version_id = uuid.uuid4().hex
        storage_key = self._report_key(owner, report_id, 1)
        self._store_report_bytes(storage_key, content, owner, report_id, 1)

        try:
            report = self._reports.create_report(
                report_id=report_id,
                research_id=research_id,
                owner_id=owner,
                title=clean_title,
                format="md",
                version=1,
                status=status.value,
                critic_score=critic_score,
                verification_status=verification_status.value,
                storage_reference=storage_key,
            )
            self._reports.create_version(
                version_id=version_id,
                report_id=report_id,
                owner_id=owner,
                version=1,
                status=status.value,
                content_markdown=content,
                critic_score=critic_score,
                verification_status=verification_status.value,
                storage_reference=storage_key,
                change_summary=None,
            )
        except Exception:
            self._reports.delete_report(report_id, owner)
            self._safe_delete(storage_key)
            raise

        logger.info("storage.report_created", extra={"report_id": report_id})
        return report

    def add_report_version(
        self,
        report_id: str,
        owner_id: str,
        content_markdown: str,
        *,
        status: ReportVersionStatus = ReportVersionStatus.DRAFT,
        critic_score: Optional[int] = None,
        verification_status: ReportVerificationStatus = ReportVerificationStatus.UNVERIFIED,
        change_summary: Optional[str] = None,
    ) -> Dict[str, Any]:
        owner = self._require_owner(owner_id)
        report = self._require_report(report_id, owner)
        content = content_markdown or ""
        if not content.strip():
            raise BadRequestError("Cannot create an empty report version.")

        number = int(report["version"]) + 1
        version_id = uuid.uuid4().hex
        storage_key = self._report_key(owner, report_id, number)
        self._store_report_bytes(storage_key, content, owner, report_id, number)

        try:
            version = self._reports.create_version(
                version_id=version_id,
                report_id=report_id,
                owner_id=owner,
                version=number,
                status=status.value,
                content_markdown=content,
                critic_score=critic_score,
                verification_status=verification_status.value,
                storage_reference=storage_key,
                change_summary=change_summary,
            )
            self._reports.update_report(
                report_id,
                owner,
                {
                    "version": number,
                    "status": status.value,
                    "critic_score": critic_score,
                    "verification_status": verification_status.value,
                    "format": "md",
                    "storage_reference": storage_key,
                },
            )
        except Exception:
            self._reports.delete_version(version_id, owner)
            self._safe_delete(storage_key)
            raise

        logger.info("storage.report_version_created", extra={"report_id": report_id, "version": number})
        return version

    def get_report(self, report_id: str, owner_id: str) -> Dict[str, Any]:
        owner = self._require_owner(owner_id)
        return self._require_report(report_id, owner)

    def list_report_versions(
        self, report_id: str, owner_id: str
    ) -> List[Dict[str, Any]]:
        owner = self._require_owner(owner_id)
        self._require_report(report_id, owner)
        return self._reports.list_versions(report_id, owner)

    def download_report_version(
        self, report_id: str, version_id: str, owner_id: str
    ) -> Tuple[bytes, Dict[str, Any]]:
        owner = self._require_owner(owner_id)
        self._require_report(report_id, owner)
        version = self._reports.get_version(version_id, owner)
        if version is None or version["report_id"] != report_id:
            raise NotFoundError("Report version not found.")
        key = version.get("storage_reference")
        if not key:
            # Fall back to the stored markdown body.
            return version["content_markdown"].encode("utf-8"), version
        try:
            payload = self._storage.download(key)
        except FileNotFoundError as exc:
            raise NotFoundError("Report version file is missing.") from exc
        return payload, version

    def delete_report(self, report_id: str, owner_id: str) -> None:
        owner = self._require_owner(owner_id)
        self._require_report(report_id, owner)
        versions = self._reports.list_versions(report_id, owner)
        for version in versions:
            reference = version.get("storage_reference")
            if reference:
                self._safe_delete(reference)
        self._reports.delete_report(report_id, owner)
        logger.info("storage.report_deleted", extra={"report_id": report_id})

    # ── Backup / recovery ─────────────────────────────────────────────────

    def backup(self) -> str:
        backup = getattr(self._storage, "backup", None)
        if backup is None:
            raise FileProcessingError("The configured storage backend does not support backup.")
        try:
            return backup()
        except Exception as exc:
            logger.exception("storage.backup_failed")
            raise FileProcessingError("Could not create a storage backup.") from exc

    def restore(self, backup_id: str) -> int:
        restore = getattr(self._storage, "restore", None)
        if restore is None:
            raise FileProcessingError("The configured storage backend does not support restore.")
        try:
            return restore(backup_id)
        except FileNotFoundError:
            raise
        except Exception as exc:
            logger.exception("storage.restore_failed")
            raise FileProcessingError("Could not restore storage from backup.") from exc


__all__ = ["StorageService", "SecureLocalStorageService"]
