"""Document upload, listing, status and deletion endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Path, Query, UploadFile

from app.errors import AuthenticationError
from app.schemas.common import PaginationMeta
from app.schemas.documents import (
    DeletedDocumentResponse,
    DocumentListResponse,
    DocumentRecord,
    DocumentStatusResponse,
)
from app.schemas.enums import DocumentProcessingStatus
from app.schemas.errors import ApiError
from app.security.dependencies import get_current_user
from app.services.container import get_document_service
from app.services.document_service import DocumentService

router = APIRouter(tags=["Documents"])

DocumentIdPath = Path(
    ...,
    pattern=r"^[a-f0-9]{32}$",
    description="32-character lowercase hex document id.",
)

_ERRORS = {
    400: {"model": ApiError, "description": "Invalid file, size, type or content."},
    401: {"model": ApiError, "description": "Authentication required."},
    409: {"model": ApiError, "description": "Duplicate document content."},
    404: {"model": ApiError, "description": "Document, project or research not found."},
}


def _owner(current_user: dict) -> str:
    owner = str(current_user.get("user_id") or "").strip()
    if not owner:
        raise AuthenticationError("Authentication required.")
    return owner


def _pagination(limit: int, offset: int, total: int, returned: int) -> PaginationMeta:
    return PaginationMeta(
        limit=limit,
        offset=offset,
        total=total,
        returned=returned,
        has_more=offset + returned < total,
    )


@router.post(
    "/documents/upload",
    response_model=DocumentRecord,
    status_code=201,
    responses=_ERRORS,
    summary="Upload a research document",
)
async def upload_document(
    file: UploadFile = File(..., description="PDF, DOCX, TXT or Markdown file."),
    project_id: Optional[UUID] = Form(None, description="Optional owning project."),
    research_id: Optional[str] = Form(
        None,
        pattern=r"^[a-f0-9]{32}$",
        description="Optional research session id.",
    ),
    current_user: dict = Depends(get_current_user),
    service: DocumentService = Depends(get_document_service),
) -> DocumentRecord:
    """Validate and store one document. The recorded status is ``uploaded``.

    Indexing is not attempted here, so a successful upload never claims to be
    ``indexed``.
    """
    row = await service.upload(
        _owner(current_user),
        upload=file,
        project_id=project_id,
        research_id=research_id,
    )
    return DocumentRecord.model_validate(row)


@router.get(
    "/documents",
    response_model=DocumentListResponse,
    responses=_ERRORS,
    summary="List the caller's documents",
)
async def list_documents(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=1_000_000),
    project_id: Optional[UUID] = Query(None, description="Filter by project."),
    research_id: Optional[str] = Query(
        None, pattern=r"^[a-f0-9]{32}$", description="Filter by research session."
    ),
    status: Optional[DocumentProcessingStatus] = Query(
        None, description="Filter by processing status."
    ),
    q: Optional[str] = Query(None, max_length=200, description="Filename search."),
    created_after: Optional[datetime] = Query(None),
    created_before: Optional[datetime] = Query(None),
    sort: Literal["created_at", "original_filename", "size_bytes", "processing_status"] = Query(
        "created_at"
    ),
    order: Literal["asc", "desc"] = Query("desc"),
    current_user: dict = Depends(get_current_user),
    service: DocumentService = Depends(get_document_service),
) -> DocumentListResponse:
    rows, total = service.list_documents(
        _owner(current_user),
        project_id=str(project_id) if project_id else None,
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
    records = [DocumentRecord.model_validate(row) for row in rows]
    return DocumentListResponse(
        documents=records,
        count=len(records),
        pagination=_pagination(limit, offset, total, len(records)),
    )


@router.get(
    "/documents/{document_id}",
    response_model=DocumentRecord,
    responses=_ERRORS,
    summary="Get document metadata",
)
async def get_document(
    document_id: str = DocumentIdPath,
    current_user: dict = Depends(get_current_user),
    service: DocumentService = Depends(get_document_service),
) -> DocumentRecord:
    row = service.get_document(document_id, _owner(current_user))
    return DocumentRecord.model_validate(row)


@router.get(
    "/documents/{document_id}/status",
    response_model=DocumentStatusResponse,
    responses=_ERRORS,
    summary="Get document processing status",
)
async def get_document_status(
    document_id: str = DocumentIdPath,
    current_user: dict = Depends(get_current_user),
    service: DocumentService = Depends(get_document_service),
) -> DocumentStatusResponse:
    """Report the stored processing state and any recorded error."""
    row = service.status(document_id, _owner(current_user))
    return DocumentStatusResponse.model_validate(row)


@router.delete(
    "/documents/{document_id}",
    response_model=DeletedDocumentResponse,
    responses=_ERRORS,
    summary="Delete a document and its stored bytes",
)
async def delete_document(
    document_id: str = DocumentIdPath,
    current_user: dict = Depends(get_current_user),
    service: DocumentService = Depends(get_document_service),
) -> DeletedDocumentResponse:
    service.delete_document(document_id, _owner(current_user))
    return DeletedDocumentResponse(status="deleted", document_id=document_id)
