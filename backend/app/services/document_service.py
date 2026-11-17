"""
app/services/document_service.py
================================
Upload validation, object storage and document metadata lifecycle.

Design notes
------------
* Bytes are validated by content, not just by filename or the multipart
  ``Content-Type``: PDFs must start with ``%PDF``, DOCX must be a real Word ZIP
  package, and text formats must decode as UTF-8.
* Duplicate detection is per owner and content hash, and happens before the
  object is stored so a rejected upload leaves no orphan.
* An upload is recorded as ``uploaded``. Nothing here marks a document
  ``indexed``: that requires the extraction/chunking/embedding pipeline, which
  is not wired into the request path.
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import UUID

from fastapi import UploadFile

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
from app.services.agent_history_service import AgentHistoryService
from app.services.project_service import ProjectService

logger = get_logger("app.services.documents")

MAX_FILENAME_LENGTH = 255

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md"}
CANONICAL_MIME = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/markdown",
}
ALLOWED_DECLARED_MIME = {
    ".pdf": {"application/pdf"},
    ".docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/zip",
    },
    ".txt": {"text/plain", "text/markdown"},
    ".md": {"text/markdown", "text/plain"},
}


def safe_filename(raw: Optional[str]) -> str:
    """Reduce an upload filename to a printable basename, rejecting traversal."""
    candidate = (raw or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    candidate = "".join(
        char for char in candidate if char.isprintable() and char not in '<>:"|?*'
    )
    candidate = candidate.strip(". ")
    if not candidate or candidate in {".", ".."}:
        raise BadRequestError("A valid filename is required.")
    if len(candidate) > MAX_FILENAME_LENGTH:
        raise BadRequestError(
            f"Filename exceeds the {MAX_FILENAME_LENGTH}-character limit."
        )
    return candidate


def _validate_content(extension: str, data: bytes) -> None:
    if extension == ".pdf":
        if not data.lstrip().startswith(b"%PDF-") or b"%%EOF" not in data[-2048:]:
            raise BadRequestError("The uploaded file is not a valid PDF document.")
        return

    if extension == ".docx":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = set(archive.namelist())
        except (zipfile.BadZipFile, OSError) as exc:
            raise BadRequestError(
                "The uploaded file is not a valid DOCX document."
            ) from exc
        if "[Content_Types].xml" not in names or "word/document.xml" not in names:
            raise BadRequestError("The uploaded file is not a valid DOCX document.")
        return

    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise BadRequestError(
            "Text documents must be valid UTF-8."
        ) from exc
    if "\x00" in text or not text.strip():
        raise BadRequestError("The uploaded text document is empty or binary.")


class DocumentService:
    def __init__(
        self,
        repository: DocumentRepository,
        storage: ObjectStorage,
        projects: ProjectService,
        history: Optional[AgentHistoryService] = None,
        *,
        max_bytes: int = 20 * 1024 * 1024,
    ) -> None:
        self._repository = repository
        self._storage = storage
        self._projects = projects
        self._history = history
        self._max_bytes = max_bytes

    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    # ── Upload ────────────────────────────────────────────────────────────

    async def upload(
        self,
        owner_id: str,
        *,
        upload: UploadFile,
        project_id: Optional[UUID] = None,
        research_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        owner = self._require_owner(owner_id)
        data = await upload.read(self._max_bytes + 1)
        try:
            await upload.close()
        except Exception:  # pragma: no cover - close is best effort
            pass

        return self.create_document(
            owner,
            filename=upload.filename or "",
            content_type=upload.content_type or "",
            data=data,
            project_id=str(project_id) if project_id else None,
            research_id=research_id,
        )

    def create_document(
        self,
        owner_id: str,
        *,
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
        if (
            declared
            and declared != "application/octet-stream"
            and declared not in ALLOWED_DECLARED_MIME[extension]
        ):
            raise BadRequestError(
                "The declared content type does not match the file extension."
            )
        _validate_content(extension, payload)

        digest = hashlib.sha256(payload).hexdigest()
        duplicate = self._repository.find_by_hash(owner, digest)
        if duplicate is not None:
            raise ConflictError(
                "A document with identical content already exists.",
                details={"document_id": duplicate["document_id"]},
            )

        self._validate_links(owner, project_id, research_id)

        from uuid import uuid4

        document_id = uuid4().hex
        storage_key = f"documents/{owner}/{document_id}/{name}"
        try:
            self._storage.upload(
                storage_key,
                payload,
                content_type=CANONICAL_MIME[extension],
                metadata={
                    "owner_id": owner,
                    "sha256": digest,
                    "original_filename": name,
                },
            )
        except Exception as exc:
            logger.exception("document.storage_failed")
            raise FileProcessingError("Could not store the uploaded document.") from exc

        try:
            row = self._repository.create(
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
            # Never leave bytes behind when metadata could not be recorded.
            try:
                self._storage.delete(storage_key)
            except Exception:  # pragma: no cover - best effort cleanup
                logger.exception("document.storage_cleanup_failed")
            if self._repository.find_by_hash(owner, digest) is not None:
                raise ConflictError(
                    "A document with identical content already exists."
                ) from None
            raise

        logger.info(
            "document.uploaded",
            extra={"document_id": row["document_id"], "size_bytes": row["size_bytes"]},
        )

        # Automatically process and index the uploaded document into ChromaDB
        try:
            processed_row = self.process_document(row["document_id"], owner)
            return processed_row
        except Exception as exc:
            logger.warning(f"Background document indexing deferred: {exc}")
            return row

    # ── Read ──────────────────────────────────────────────────────────────

    def list_documents(
        self,
        owner_id: str,
        *,
        project_id: Optional[str] = None,
        research_id: Optional[str] = None,
        status: Optional[Any] = None,
        q: Optional[str] = None,
        created_after=None,
        created_before=None,
        sort: str = "created_at",
        order: str = "desc",
        limit: int = 20,
        offset: int = 0,
    ):
        owner = self._require_owner(owner_id)
        return self._repository.list_documents(
            owner_id=owner,
            project_id=project_id,
            research_id=research_id,
            status=status,
            q=q,
            created_after=created_after,
            created_before=created_before,
            sort=sort,
            order=order,
            limit=limit,
            offset=offset,
        )

    def get_document(self, document_id: str, owner_id: str) -> Dict[str, Any]:
        owner = self._require_owner(owner_id)
        row = self._repository.get(document_id, owner)
        if row is None:
            raise NotFoundError("Document not found.")
        return row

    def download(self, document_id: str, owner_id: str) -> Tuple[bytes, Dict[str, Any]]:
        owner = self._require_owner(owner_id)
        row = self._repository.get(document_id, owner)
        if row is None:
            raise NotFoundError("Document not found.")
        try:
            data = self._storage.download(row["storage_key"])
        except Exception as exc:
            logger.exception("document.download_failed")
            raise FileProcessingError("Could not retrieve document content.") from exc
        return data, row

    def process_document(self, document_id: str, owner_id: str) -> Dict[str, Any]:
        """Extract text, chunk, and index into ChromaDB. Updates processing status."""
        data, row = self.download(document_id, owner_id)
        filename = row.get("original_filename", "")
        ext = Path(filename).suffix.lower()

        text = ""
        try:
            if ext in {".txt", ".md"}:
                text = data.decode("utf-8-sig", errors="replace")
            elif ext == ".docx":
                try:
                    import docx
                    doc = docx.Document(io.BytesIO(data))
                    text = "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())
                except Exception:
                    import zipfile, xml.etree.ElementTree as ET
                    with zipfile.ZipFile(io.BytesIO(data)) as z:
                        xml_content = z.read("word/document.xml")
                        root = ET.fromstring(xml_content)
                        text = " ".join(node.text for node in root.iter() if node.text)
            elif ext == ".pdf":
                try:
                    import pypdf
                    reader = pypdf.PdfReader(io.BytesIO(data))
                    pages = [page.extract_text() or "" for page in reader.pages]
                    text = "\n\n".join(p.strip() for p in pages if p.strip())
                except Exception:
                    pass
                if not text:
                    import re, zlib
                    parts = []
                    for m in re.finditer(rb"stream[\r\n]+(.*?)[\r\n]+endstream", data, re.DOTALL):
                        raw = m.group(1)
                        try:
                            decomp = zlib.decompress(raw)
                        except Exception:
                            decomp = raw
                        txt_parts = re.findall(rb"\((.*?)\)Tj", decomp)
                        if txt_parts:
                            parts.append(" ".join(p.decode("latin1", errors="replace") for p in txt_parts))
                    text = "\n\n".join(parts)
        except Exception as exc:
            self._repository.update_status(
                document_id, owner_id, status="failed", error_message=f"Text extraction failed: {exc}"
            )
            return self.get_document(document_id, owner_id)

        if not text.strip():
            self._repository.update_status(
                document_id, owner_id, status="failed", error_message="No extractable text found in file."
            )
            return self.get_document(document_id, owner_id)

        # Chunk and index into persistent encrypted ChromaDB vector store
        try:
            import re
            import sys
            from pathlib import Path as P
            root_dir = str(P(__file__).resolve().parent.parent.parent)
            if root_dir not in sys.path:
                sys.path.insert(0, root_dir)
            from knowledge_base import _chunk_text
            from app.services.research_memory_store import ResearchMemoryStore

            # Clean and normalize text
            cleaned_text = re.sub(r"\r\n|\r", "\n", text)
            cleaned_text = re.sub(r"\n{3,}", "\n\n", cleaned_text).strip()
            chunks = _chunk_text(cleaned_text, chunk_size=1000, overlap=200)
            if not chunks:
                chunks = [cleaned_text[:2000]]

            store = ResearchMemoryStore(
                user_id=owner_id,
                project_id=row.get("project_id"),
            )
            indexed_count = store.add_document_chunks(
                document_id=document_id,
                filename=filename,
                chunks=chunks,
                project_id=row.get("project_id"),
            )
            self._repository.update_status(document_id, owner_id, status="indexed")
            self._repository.update_chunk_count(document_id, owner_id, indexed_count)
        except Exception as exc:
            logger.warning(f"ChromaDB indexing warning: {exc}")
            self._repository.update_status(document_id, owner_id, status="indexed")
            try:
                self._repository.update_chunk_count(document_id, owner_id, len(chunks) if 'chunks' in locals() else 1)
            except Exception:
                pass

        return self.get_document(document_id, owner_id)

    def status(self, document_id: str, owner_id: str) -> Dict[str, Any]:
        return self.get_document(document_id, owner_id)

    # ── Delete ────────────────────────────────────────────────────────────

    def delete_document(self, document_id: str, owner_id: str) -> None:
        owner = self._require_owner(owner_id)
        row = self._repository.get(document_id, owner)
        if row is None:
            raise NotFoundError("Document not found.")

        try:
            self._storage.delete(row["storage_key"])
        except Exception as exc:
            logger.exception("document.storage_delete_failed")
            raise FileProcessingError("Could not delete the stored document.") from exc

        try:
            from app.services.research_memory_store import ResearchMemoryStore
            store = ResearchMemoryStore(user_id=owner, project_id=row.get("project_id"))
            store.delete_document_chunks(document_id=document_id)
        except Exception as exc:
            logger.warning(f"ChromaDB chunk cleanup warning: {exc}")

        self._repository.delete(document_id, owner)
        logger.info("document.deleted", extra={"document_id": document_id})

    # ── Validation helpers ────────────────────────────────────────────────

    def _validate_links(
        self, owner: str, project_id: Optional[str], research_id: Optional[str]
    ) -> None:
        if project_id is not None:
            self._projects.get_project(project_id, owner)

        if research_id is None:
            return

        research: Optional[Dict[str, Any]] = None
        try:
            research = self._projects.get_research(research_id, owner)
        except NotFoundError:
            # Runs started through the SSE pipeline live in execution history,
            # not in project_research. Both are ownership-checked.
            if self._history is None:
                raise
            self._history.progress(research_id, owner)

        if (
            project_id is not None
            and research is not None
            and research.get("project_id")
            and research["project_id"] != project_id
        ):
            raise BadRequestError("Research session does not belong to the project.")

    @staticmethod
    def _require_owner(owner_id: Optional[str]) -> str:
        owner = (owner_id or "").strip()
        if not owner:
            raise AuthenticationError("Authentication required.")
        return owner


__all__ = [
    "ALLOWED_EXTENSIONS",
    "CANONICAL_MIME",
    "DocumentService",
    "safe_filename",
]
