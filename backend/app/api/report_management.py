"""Report metadata, versioning and export endpoints."""

from __future__ import annotations

import re
from typing import Literal, Optional

from fastapi import APIRouter, Body, Depends, Path, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.routing import APIRoute
from starlette.convertors import Convertor, register_url_convertor

from app.errors import AuthenticationError
from app.schemas.common import PaginationMeta
from app.schemas.enums import ReportVersionStatus
from app.schemas.errors import ApiError
from app.schemas.report_management import (
    DeletedReportResponse,
    ReportExportRequest,
    ReportRecord,
    ReportVersionCreateRequest,
    ReportVersionDetail,
    ReportVersionSummary,
    ReportVersionsResponse,
    ResearchReportsResponse,
)
from app.security.dependencies import get_current_user
from app.services.container import get_report_management_service
from app.services.report_management_service import ReportManagementService


class Hex32Convertor(Convertor):
    regex = "[a-f0-9]{32}"

    def convert(self, value: str) -> str:
        return value

    def to_string(self, value: str) -> str:
        return value


try:
    register_url_convertor("hex32", Hex32Convertor())
except ValueError:
    pass


router = APIRouter(tags=["Reports"])

_REPORT_ID = r"^[a-f0-9]{32}$"
ReportIdPath = Path(..., pattern=_REPORT_ID, description="32-character report id.")
VersionIdPath = Path(..., pattern=_REPORT_ID, description="32-character version id.")
ResearchIdPath = Path(..., pattern=_REPORT_ID, description="32-character research id.")

_ERRORS = {
    400: {"model": ApiError, "description": "Invalid report content or export."},
    401: {"model": ApiError, "description": "Authentication required."},
    404: {"model": ApiError, "description": "Report, version or research not found."},
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


@router.get(
    "/research/{research_id}/reports",
    response_model=ResearchReportsResponse,
    responses=_ERRORS,
    summary="List reports for a research run",
)
async def list_research_reports(
    research_id: str = ResearchIdPath,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=1_000_000),
    status: Optional[ReportVersionStatus] = Query(None),
    sort: Literal["created_at", "updated_at", "title", "status", "version"] = Query(
        "created_at"
    ),
    order: Literal["asc", "desc"] = Query("desc"),
    current_user: dict = Depends(get_current_user),
    service: ReportManagementService = Depends(get_report_management_service),
) -> ResearchReportsResponse:
    rows, total = service.list_reports(
        research_id,
        _owner(current_user),
        status=status,
        sort=sort,
        order=order,
        limit=limit,
        offset=offset,
    )
    reports = [ReportRecord.model_validate(row) for row in rows]
    return ResearchReportsResponse(
        research_id=research_id,
        reports=reports,
        count=len(reports),
        pagination=_pagination(limit, offset, total, len(reports)),
    )


@router.get(
    "/reports/{report_id:hex32}",
    response_model=ReportRecord,
    responses=_ERRORS,
    summary="Get report metadata",
)
async def get_report(
    report_id: str = ReportIdPath,
    current_user: dict = Depends(get_current_user),
    service: ReportManagementService = Depends(get_report_management_service),
) -> ReportRecord:
    return ReportRecord.model_validate(
        service.get_report(report_id, _owner(current_user))
    )


@router.get(
    "/reports/{report_id:hex32}/versions",
    response_model=ReportVersionsResponse,
    responses=_ERRORS,
    summary="List immutable report versions",
)
async def list_report_versions(
    report_id: str = ReportIdPath,
    current_user: dict = Depends(get_current_user),
    service: ReportManagementService = Depends(get_report_management_service),
) -> ReportVersionsResponse:
    rows = service.list_versions(report_id, _owner(current_user))
    versions = [ReportVersionSummary.model_validate(row) for row in rows]
    return ReportVersionsResponse(
        report_id=report_id, versions=versions, count=len(versions)
    )


@router.get(
    "/reports/{report_id:hex32}/versions/{version_id:hex32}",
    response_model=ReportVersionDetail,
    responses=_ERRORS,
    summary="Get one report version including its Markdown body",
)
async def get_report_version(
    report_id: str = ReportIdPath,
    version_id: str = VersionIdPath,
    current_user: dict = Depends(get_current_user),
    service: ReportManagementService = Depends(get_report_management_service),
) -> ReportVersionDetail:
    return ReportVersionDetail.model_validate(
        service.get_version(report_id, version_id, _owner(current_user))
    )


@router.post(
    "/reports/{report_id:hex32}/export",
    responses={
        **_ERRORS,
        500: {"model": ApiError, "description": "Export generation failed."},
    },
    summary="Export a report version as Markdown, DOCX or PDF",
)
async def export_report_version(
    report_id: str = ReportIdPath,
    payload: ReportExportRequest = Body(...),
    current_user: dict = Depends(get_current_user),
    service: ReportManagementService = Depends(get_report_management_service),
) -> FileResponse:
    path, filename, media_type = await run_in_threadpool(
        service.export_report, report_id, _owner(current_user), payload
    )
    return FileResponse(
        path,
        media_type=media_type,
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post(
    "/reports/{report_id:hex32}/versions",
    response_model=ReportVersionDetail,
    status_code=201,
    responses=_ERRORS,
    summary="Create a new immutable report version",
)
async def create_report_version(
    report_id: str = ReportIdPath,
    payload: ReportVersionCreateRequest = Body(...),
    current_user: dict = Depends(get_current_user),
    service: ReportManagementService = Depends(get_report_management_service),
) -> ReportVersionDetail:
    """Create a new version. An approved version is never overwritten."""
    row = service.create_version(report_id, _owner(current_user), payload)
    return ReportVersionDetail.model_validate(row)


@router.delete(
    "/reports/{report_id:hex32}",
    response_model=DeletedReportResponse,
    responses=_ERRORS,
    summary="Delete a report and all of its versions",
)
async def delete_report(
    report_id: str = ReportIdPath,
    current_user: dict = Depends(get_current_user),
    service: ReportManagementService = Depends(get_report_management_service),
) -> DeletedReportResponse:
    service.delete_report(report_id, _owner(current_user))
    return DeletedReportResponse(status="deleted", report_id=report_id)
