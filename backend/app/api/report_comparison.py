"""
app/api/report_comparison.py
============================
API endpoints for Report Comparison & Analysis.

Enables comparing any two authorized research reports across all combinations:
- Uploaded Report (PDF, DOCX, TXT, MD) vs Generated Report (Research History)
- Generated Report vs Generated Report
- Uploaded Report vs Uploaded Report
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional
from fastapi import APIRouter, Body, Depends, Path, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.errors import AuthenticationError
from app.schemas.errors import ApiError
from app.security.dependencies import get_current_user
from app.services.container import get_report_comparison_service
from app.services.report_comparison_service import ReportComparisonService
from app.infrastructure import postgres_bridge

router = APIRouter(tags=["Report Comparison"])

_ERRORS = {
    400: {"model": ApiError, "description": "Invalid report selection or parameters."},
    401: {"model": ApiError, "description": "Authentication required."},
    404: {"model": ApiError, "description": "Report or comparison session not found."},
}


class CreateComparisonRequest(BaseModel):
    report_a_id: str = Field(..., min_length=1, description="ID of Report A (document_id or session_id)")
    report_a_type: Literal["uploaded", "generated"] = Field("uploaded", description="Type of Report A")
    report_b_id: str = Field(..., min_length=1, description="ID of Report B (document_id or session_id)")
    report_b_type: Literal["uploaded", "generated"] = Field("generated", description="Type of Report B")
    title: Optional[str] = Field(None, description="Optional custom title for the comparison")


class ComparisonChatRequest(BaseModel):
    question: str = Field(..., min_length=1, description="Question to ask across both reports")


def _owner(current_user: dict) -> str:
    owner = str(current_user.get("user_id") or "").strip()
    if not owner:
        raise AuthenticationError("Authentication required.")
    return owner


@router.post(
    "/report-comparisons",
    status_code=201,
    responses=_ERRORS,
    summary="Compare two research reports",
)
async def create_report_comparison(
    req: CreateComparisonRequest,
    current_user: dict = Depends(get_current_user),
    service: ReportComparisonService = Depends(get_report_comparison_service),
) -> Dict[str, Any]:
    """Execute cross-examination and comparative analysis between Report A and Report B."""
    user_id = _owner(current_user)
    return service.compare(
        report_a_id=req.report_a_id,
        report_a_type=req.report_a_type,
        report_b_id=req.report_b_id,
        report_b_type=req.report_b_type,
        user_id=user_id,
        custom_title=req.title,
    )


@router.get(
    "/report-comparisons",
    responses=_ERRORS,
    summary="List saved report comparisons",
)
async def list_report_comparisons(
    limit: int = Query(50, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
) -> Dict[str, Any]:
    """List comparison sessions owned by the authenticated caller."""
    user_id = _owner(current_user)
    items = postgres_bridge.list_report_comparisons(user_id, limit=limit)
    return {"comparisons": items, "total": len(items)}


@router.get(
    "/report-comparisons/{comparison_id}",
    responses=_ERRORS,
    summary="Get report comparison details",
)
async def get_report_comparison(
    comparison_id: str = Path(..., description="Comparison ID"),
    current_user: dict = Depends(get_current_user),
) -> Dict[str, Any]:
    """Retrieve full analysis, dimensions, differences and metadata for a comparison."""
    user_id = _owner(current_user)
    res = postgres_bridge.get_report_comparison(comparison_id, user_id)
    if not res:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Report comparison not found or unauthorized.")
    return res


@router.delete(
    "/report-comparisons/{comparison_id}",
    responses=_ERRORS,
    summary="Delete a report comparison",
)
async def delete_report_comparison(
    comparison_id: str = Path(..., description="Comparison ID"),
    current_user: dict = Depends(get_current_user),
) -> Dict[str, Any]:
    """Delete a report comparison and its associated conversation history."""
    user_id = _owner(current_user)
    success = postgres_bridge.delete_report_comparison(comparison_id, user_id)
    return {"deleted": success, "comparison_id": comparison_id}


@router.post(
    "/report-comparisons/{comparison_id}/chat",
    responses=_ERRORS,
    summary="Chat across both compared reports",
)
async def chat_comparison(
    comparison_id: str = Path(..., description="Comparison ID"),
    req: ComparisonChatRequest = Body(...),
    current_user: dict = Depends(get_current_user),
    service: ReportComparisonService = Depends(get_report_comparison_service),
) -> Dict[str, Any]:
    """Ask a natural-language question grounded in both active reports."""
    user_id = _owner(current_user)
    return service.chat(comparison_id, user_id, req.question)


@router.get(
    "/report-comparisons/{comparison_id}/messages",
    responses=_ERRORS,
    summary="List chat messages for a report comparison",
)
async def list_comparison_messages(
    comparison_id: str = Path(..., description="Comparison ID"),
    current_user: dict = Depends(get_current_user),
) -> Dict[str, Any]:
    """Retrieve conversation messages for this comparison session."""
    user_id = _owner(current_user)
    messages = postgres_bridge.list_comparison_messages(comparison_id, user_id)
    return {"messages": messages, "count": len(messages)}


@router.get(
    "/report-comparisons/{comparison_id}/export",
    responses=_ERRORS,
    summary="Export comparison analysis to PDF, DOCX or Markdown",
)
@router.post(
    "/report-comparisons/{comparison_id}/export",
    responses=_ERRORS,
    summary="Export comparison analysis to PDF, DOCX or Markdown",
)
async def export_comparison_report(
    comparison_id: str = Path(..., description="Comparison ID"),
    format: str = Query("pdf", description="pdf, docx, or markdown"),
    current_user: dict = Depends(get_current_user),
    service: ReportComparisonService = Depends(get_report_comparison_service),
) -> FileResponse:
    """Download the complete comparison analysis report."""
    user_id = _owner(current_user)
    path, filename, media_type = service.export(comparison_id, user_id, format)
    return FileResponse(
        path,
        media_type=media_type,
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
