"""Document upload metadata and status schemas."""

from __future__ import annotations

from typing import Annotated, List, Optional

from pydantic import Field, StringConstraints

from app.schemas.base import FileName, OutputSchema, Sha256Hex, UtcDatetime
from app.schemas.common import PaginationMeta
from app.schemas.enums import DocumentProcessingStatus

DocumentId = Annotated[
    str, StringConstraints(pattern=r"^[a-f0-9]{32}$", min_length=32, max_length=32)
]


class DocumentRecord(OutputSchema):
    """A stored document and its metadata. No processing result is implied."""

    document_id: DocumentId
    owner_id: str
    project_id: Optional[str] = None
    research_id: Optional[str] = None
    original_filename: FileName
    mime_type: str = Field(..., min_length=3, max_length=120)
    size_bytes: int = Field(..., ge=0, le=104_857_600)
    sha256: Sha256Hex
    storage_key: str = Field(..., min_length=1, max_length=512)
    processing_status: DocumentProcessingStatus
    error_code: Optional[str] = Field(None, max_length=80)
    error_message: Optional[str] = Field(None, max_length=500)
    created_at: UtcDatetime
    updated_at: UtcDatetime


class DocumentListResponse(OutputSchema):
    documents: List[DocumentRecord]
    count: int = Field(ge=0)
    pagination: PaginationMeta


class DocumentStatusResponse(OutputSchema):
    document_id: DocumentId
    processing_status: DocumentProcessingStatus
    error_code: Optional[str] = Field(None, max_length=80)
    error_message: Optional[str] = Field(None, max_length=500)
    updated_at: UtcDatetime


class DeletedDocumentResponse(OutputSchema):
    status: str
    document_id: str


__all__ = [
    "DeletedDocumentResponse",
    "DocumentId",
    "DocumentListResponse",
    "DocumentRecord",
    "DocumentStatusResponse",
]
