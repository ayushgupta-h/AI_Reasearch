"""Document upload, listing, status and deletion endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Path, Query, Response, UploadFile
from pydantic import BaseModel, Field

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
from app.services.container import get_document_service, get_document_chat_service
from app.services.document_service import DocumentService
from app.services.document_chat_service import DocumentChatService

class DocumentChatRequest(BaseModel):
    question: str = Field(..., min_length=1, description="User question about the document.")
    conversation_id: Optional[str] = Field(None, description="Optional ongoing conversation ID.")

class MultiDocumentChatRequest(BaseModel):
    question: str = Field(..., min_length=1, description="Question across multiple documents.")
    document_ids: List[str] = Field(..., min_length=1, description="List of document IDs to analyze.")
    conversation_id: Optional[str] = Field(None, description="Optional ongoing conversation ID.")

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
    "/documents",
    response_model=DocumentRecord,
    status_code=201,
    responses=_ERRORS,
    summary="Upload a research document (alias)",
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
    project_id: Optional[str] = Form(None, description="Optional owning project."),
    research_id: Optional[str] = Form(None, description="Optional research session id."),
    current_user: dict = Depends(get_current_user),
    service: DocumentService = Depends(get_document_service),
) -> DocumentRecord:
    """Validate, store, and index one research document."""
    clean_project_id: Optional[UUID] = None
    if project_id is not None:
        val = project_id.strip()
        if val and val.lower() not in {"null", "undefined", "none"}:
            try:
                clean_project_id = UUID(val)
            except (ValueError, TypeError):
                from fastapi import HTTPException
                raise HTTPException(status_code=422, detail="Invalid project_id")

    clean_research_id: Optional[str] = None
    if research_id and research_id.strip() and research_id.strip().lower() not in {"null", "undefined", "none", ""}:
        clean_research_id = research_id.strip()

    row = await service.upload(
        _owner(current_user),
        upload=file,
        project_id=clean_project_id,
        research_id=clean_research_id,
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
    "/documents/{document_id}/index-status",
    response_model=DocumentStatusResponse,
    responses=_ERRORS,
    summary="Get document indexing status alias",
)
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


@router.get(
    "/documents/{document_id}/download",
    responses=_ERRORS,
    summary="Download a stored document",
)
async def download_document(
    document_id: str = DocumentIdPath,
    current_user: dict = Depends(get_current_user),
    service: DocumentService = Depends(get_document_service),
) -> Response:
    """Stream stored bytes for an owned document."""
    data, row = service.download(document_id, _owner(current_user))
    filename = row.get("original_filename", "document")
    mime_type = row.get("mime_type", "application/octet-stream")
    return Response(
        content=data,
        media_type=mime_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post(
    "/documents/{document_id}/reindex",
    response_model=DocumentRecord,
    responses=_ERRORS,
    summary="Reindex an uploaded document alias",
)
@router.post(
    "/documents/{document_id}/process",
    response_model=DocumentRecord,
    responses=_ERRORS,
    summary="Retry extraction and vector indexing for a document",
)
async def process_document(
    document_id: str = DocumentIdPath,
    current_user: dict = Depends(get_current_user),
    service: DocumentService = Depends(get_document_service),
) -> DocumentRecord:
    """Extract text from the document, chunk it, and index it into ChromaDB."""
    row = service.process_document(document_id, _owner(current_user))
    return DocumentRecord.model_validate(row)


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


# ── Document Preview & Chat With Documents ─────────────────────────────────

@router.get(
    "/documents/{document_id}/preview",
    responses=_ERRORS,
    summary="Get structured text preview and page breakdown for a document",
)
async def get_document_preview(
    document_id: str = DocumentIdPath,
    current_user: dict = Depends(get_current_user),
    service: DocumentService = Depends(get_document_service),
) -> Dict[str, Any]:
    return service.get_document_preview(document_id, _owner(current_user))


@router.post(
    "/documents/{document_id}/chat",
    responses=_ERRORS,
    summary="Ask a question grounded in a single research document",
)
async def chat_with_document(
    payload: DocumentChatRequest,
    document_id: str = DocumentIdPath,
    current_user: dict = Depends(get_current_user),
    chat_svc: DocumentChatService = Depends(get_document_chat_service),
) -> Dict[str, Any]:
    return chat_svc.chat_with_document(
        user_id=_owner(current_user),
        document_id=document_id,
        question=payload.question,
        conversation_id=payload.conversation_id,
    )


@router.post(
    "/documents/chat/multi",
    responses=_ERRORS,
    summary="Ask a question comparing multiple research documents",
)
async def chat_multi_document(
    payload: MultiDocumentChatRequest,
    current_user: dict = Depends(get_current_user),
    chat_svc: DocumentChatService = Depends(get_document_chat_service),
) -> Dict[str, Any]:
    return chat_svc.chat_multi_document(
        user_id=_owner(current_user),
        document_ids=payload.document_ids,
        question=payload.question,
        conversation_id=payload.conversation_id,
    )


@router.get(
    "/documents/{document_id}/conversations",
    responses=_ERRORS,
    summary="List chat conversations for a document",
)
async def list_document_conversations(
    document_id: str = DocumentIdPath,
    current_user: dict = Depends(get_current_user),
    chat_svc: DocumentChatService = Depends(get_document_chat_service),
) -> List[Dict[str, Any]]:
    return chat_svc.list_conversations(_owner(current_user), document_id=document_id)


@router.get(
    "/documents/{document_id}/conversations/{conversation_id}",
    responses=_ERRORS,
    summary="Get conversation history and citations for a document chat",
)
async def get_document_conversation(
    conversation_id: str,
    document_id: str = DocumentIdPath,
    current_user: dict = Depends(get_current_user),
    chat_svc: DocumentChatService = Depends(get_document_chat_service),
) -> Dict[str, Any]:
    return chat_svc.get_conversation_history(conversation_id, _owner(current_user))


@router.delete(
    "/documents/{document_id}/conversations/{conversation_id}",
    responses=_ERRORS,
    summary="Delete a document chat conversation",
)
async def delete_document_conversation(
    conversation_id: str,
    document_id: str = DocumentIdPath,
    current_user: dict = Depends(get_current_user),
    chat_svc: DocumentChatService = Depends(get_document_chat_service),
) -> Dict[str, Any]:
    success = chat_svc.delete_conversation(conversation_id, _owner(current_user))
    return {"status": "deleted" if success else "not_found", "conversation_id": conversation_id}
