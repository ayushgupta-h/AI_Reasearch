"""Report artifact endpoints: download, sharing and multi-format export."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from app.schemas.errors import ApiError
from app.schemas.requests import ExportRequest
from app.schemas.responses import ShareTokenResponse
from app.security.dependencies import get_current_user, get_optional_user, user_from_query_token
from app.services.container import get_report_service
from app.services.report_service import ReportService

router = APIRouter()

_AUTH_ERRORS = {
    401: {"model": ApiError, "description": "Authentication required."},
    403: {"model": ApiError, "description": "Report not owned by the caller."},
    404: {"model": ApiError, "description": "Report file not found."},
}


@router.get(
    "/reports/{filename:path}/share",
    response_model=ShareTokenResponse,
    tags=["Reports"],
    responses=_AUTH_ERRORS,
)
async def get_report_share_link(
    filename: str,
    current_user: dict = Depends(get_current_user),
    service: ReportService = Depends(get_report_service),
) -> ShareTokenResponse:
    """Generate a read-only, expiring share token for a report the caller owns."""
    return ShareTokenResponse(**service.share_link(filename, current_user))


@router.get(
    "/reports/{filename:path}",
    tags=["Reports"],
    responses=_AUTH_ERRORS,
)
async def get_report_file(
    filename: str,
    share: Optional[str] = Query(None),
    token: Optional[str] = Query(None),
    current_user: Optional[dict] = Depends(get_optional_user),
    service: ReportService = Depends(get_report_service),
) -> FileResponse:
    """Serve a generated research report with ownership or share-token checks."""
    effective_user = current_user or user_from_query_token(token)
    path, media_type, read_only = service.authorize_download(
        filename, user=effective_user, share_token=share
    )
    headers = {"X-Read-Only": "true"} if read_only else None
    return FileResponse(
        path, media_type=media_type, filename=path.name, headers=headers
    )


@router.post(
    "/export",
    tags=["Reports"],
    responses={
        400: {"model": ApiError, "description": "Empty or unsupported export."},
        500: {"model": ApiError, "description": "Export generation failed."},
    },
)
async def export_report(
    request: ExportRequest,
    current_user: Optional[dict] = Depends(get_optional_user),
    service: ReportService = Depends(get_report_service),
) -> FileResponse:
    """Export report draft content to PDF, DOCX, Markdown or TXT.

    Rendering is CPU-bound and runs in a worker thread so it cannot block the
    async event loop.
    """
    _ = current_user
    path, filename, media_type = await run_in_threadpool(service.export, request)
    return FileResponse(
        path,
        media_type=media_type,
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
