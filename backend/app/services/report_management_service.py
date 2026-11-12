"""
app/services/report_management_service.py
=========================================
Report metadata, immutable versions and export orchestration.

The actual document rendering is delegated to the existing
:class:`app.services.report_service.ReportService`, which uses
``report_exporter.ReportExporter`` for Markdown, DOCX and PDF output.

A new version is always a new row: creating a draft for an approved report
never rewrites the approved body. Uploads go through the shared
``ObjectStorage`` port; exports keep the existing exporter's filesystem
behaviour.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.errors import (
    AuthenticationError,
    BadRequestError,
    FileProcessingError,
    NotFoundError,
)
from app.infrastructure.ports import ObjectStorage
from app.observability import get_logger
from app.repositories.reports import ReportRepository
from app.schemas.report_management import (
    ReportExportRequest,
    ReportVersionCreateRequest,
)
from app.schemas.enums import (
    ReportFormat,
    ReportVerificationStatus,
    ReportVersionStatus,
)
from app.schemas.requests import ExportRequest
from app.services.agent_history_service import AgentHistoryService
from app.services.project_service import ProjectService
from app.services.report_service import ReportService

logger = get_logger("app.services.report_management")

_MAX_TITLE = 200
_MIN_TITLE = 3


def derive_verification_status(
    verified: Optional[int], total: Optional[int]
) -> ReportVerificationStatus:
    """Honest verification state from real citation counts.

    Missing counts mean validation did not report a result, so the state stays
    ``unverified`` rather than claiming success.
    """
    if not total or total <= 0:
        return ReportVerificationStatus.UNVERIFIED
    verified = max(int(verified or 0), 0)
    if verified >= total:
        return ReportVerificationStatus.VERIFIED
    if verified > 0:
        return ReportVerificationStatus.PARTIALLY_VERIFIED
    return ReportVerificationStatus.FAILED


class ReportManagementService:
    def __init__(
        self,
        repository: ReportRepository,
        storage: ObjectStorage,
        projects: Optional[ProjectService] = None,
        history: Optional[AgentHistoryService] = None,
        exporter: Optional[ReportService] = None,
    ) -> None:
        self._repository = repository
        self._storage = storage
        self._projects = projects
        self._history = history
        self._exporter = exporter

    # ── Creation ──────────────────────────────────────────────────────────

    def create_report(
        self,
        owner_id: str,
        research_id: str,
        title: str,
        content_markdown: str,
        *,
        critic_score: Optional[int] = None,
        verification_status: ReportVerificationStatus = ReportVerificationStatus.UNVERIFIED,
        status: ReportVersionStatus = ReportVersionStatus.DRAFT,
    ) -> Dict[str, Any]:
        """Create a report plus its first version (version 1, draft by default)."""
        owner = self._require_owner(owner_id)
        clean_title = (title or "").strip()
        if not (_MIN_TITLE <= len(clean_title) <= _MAX_TITLE):
            raise BadRequestError(
                f"Report title must be between {_MIN_TITLE} and {_MAX_TITLE} characters."
            )
        content = content_markdown or ""
        if not content.strip():
            raise BadRequestError("Cannot create an empty report.")

        report_id = uuid.uuid4().hex
        version_id = uuid.uuid4().hex
        storage_key = f"reports/{owner}/{report_id}/v1.md"
        self._store_markdown(storage_key, content, owner, report_id, 1)

        try:
            report = self._repository.create_report(
                report_id=report_id,
                research_id=research_id,
                owner_id=owner,
                title=clean_title,
                format=ReportFormat.MD.value,
                version=1,
                status=status.value,
                critic_score=critic_score,
                verification_status=verification_status.value,
                storage_reference=storage_key,
            )
            self._repository.create_version(
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
            self._repository.delete_report(report_id, owner)
            self._safe_delete(storage_key)
            raise

        logger.info(
            "report.created",
            extra={"report_id": report_id, "research_id": research_id},
        )
        return report

    def register_from_run(
        self,
        *,
        owner_id: Optional[str],
        research_id: str,
        title: str,
        content_markdown: str,
        critic_score: Optional[int] = None,
        citations_verified: Optional[int] = None,
        citations_total: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """Record a completed pipeline run's real report, if it produced one."""
        if not owner_id or not (content_markdown or "").strip():
            return None
        return self.create_report(
            owner_id,
            research_id,
            title,
            content_markdown,
            critic_score=critic_score,
            verification_status=derive_verification_status(
                citations_verified, citations_total
            ),
        )

    # ── Reads ─────────────────────────────────────────────────────────────

    def list_reports(
        self,
        research_id: str,
        owner_id: str,
        *,
        status: Optional[ReportVersionStatus] = None,
        sort: str = "created_at",
        order: str = "desc",
        limit: int = 20,
        offset: int = 0,
    ) -> Tuple[List[Dict[str, Any]], int]:
        owner = self._require_owner(owner_id)
        rows, total = self._repository.list_reports(
            research_id=research_id,
            owner_id=owner,
            status=status,
            sort=sort,
            order=order,
            limit=limit,
            offset=offset,
        )
        if total == 0:
            # Distinguish "owned research with no reports" from someone else's
            # or a nonexistent research id.
            self._ensure_research_owned(research_id, owner)
        return rows, total

    def get_report(self, report_id: str, owner_id: str) -> Dict[str, Any]:
        return self._require_report(report_id, self._require_owner(owner_id))

    def list_versions(self, report_id: str, owner_id: str) -> List[Dict[str, Any]]:
        owner = self._require_owner(owner_id)
        self._require_report(report_id, owner)
        return self._repository.list_versions(report_id, owner)

    def get_version(
        self, report_id: str, version_id: str, owner_id: str
    ) -> Dict[str, Any]:
        owner = self._require_owner(owner_id)
        self._require_report(report_id, owner)
        row = self._repository.get_version(version_id, owner)
        if row is None or row["report_id"] != report_id:
            raise NotFoundError("Report version not found.")
        return row

    # ── Versioning ────────────────────────────────────────────────────────

    def create_version(
        self,
        report_id: str,
        owner_id: str,
        payload: ReportVersionCreateRequest,
    ) -> Dict[str, Any]:
        owner = self._require_owner(owner_id)
        report = self._require_report(report_id, owner)
        content = payload.content_markdown
        if not content.strip():
            raise BadRequestError("Cannot create an empty report version.")

        number = int(report["version"]) + 1
        version_id = uuid.uuid4().hex
        storage_key = f"reports/{owner}/{report_id}/v{number}.md"
        self._store_markdown(storage_key, content, owner, report_id, number)

        try:
            version = self._repository.create_version(
                version_id=version_id,
                report_id=report_id,
                owner_id=owner,
                version=number,
                status=payload.status.value,
                content_markdown=content,
                critic_score=payload.critic_score,
                verification_status=payload.verification_status.value,
                storage_reference=storage_key,
                change_summary=payload.change_summary,
            )
            updated = self._repository.update_report(
                report_id,
                owner,
                {
                    "version": number,
                    "status": payload.status.value,
                    "critic_score": payload.critic_score,
                    "verification_status": payload.verification_status.value,
                    "format": ReportFormat.MD.value,
                    "storage_reference": storage_key,
                },
            )
            if updated is None:  # pragma: no cover - ownership checked above
                raise NotFoundError("Report not found.")
        except Exception:
            self._repository.delete_version(version_id, owner)
            self._safe_delete(storage_key)
            raise

        # Prior versions, including approved ones, remain untouched.
        logger.info(
            "report.version_created",
            extra={"report_id": report_id, "version": number},
        )
        return version

    # ── Export ────────────────────────────────────────────────────────────

    def export_report(
        self,
        report_id: str,
        owner_id: str,
        payload: ReportExportRequest,
    ) -> Tuple[Path, str, str]:
        owner = self._require_owner(owner_id)
        report = self._require_report(report_id, owner)

        if payload.version_id is not None:
            version = self._repository.get_version(payload.version_id, owner)
            if version is None or version["report_id"] != report_id:
                raise NotFoundError("Report version not found.")
        else:
            version = self._repository.get_version_by_number(
                report_id, int(report["version"]), owner
            )
            if version is None:  # pragma: no cover - report/version invariant
                raise NotFoundError("Report version not found.")

        if self._exporter is None:  # pragma: no cover - container always wires it
            raise FileProcessingError("The report exporter is not configured.")

        path, filename, media_type = self._exporter.export(
            ExportRequest(
                content=version["content_markdown"],
                topic=report["title"],
                format=payload.format.value,
                citation_style=payload.citation_style.value,
            )
        )

        if version["version"] == report["version"]:
            self._repository.update_report(
                report_id,
                owner,
                {
                    "format": payload.format.value,
                    "storage_reference": filename,
                },
            )
        logger.info(
            "report.exported",
            extra={"report_id": report_id, "format": payload.format.value},
        )
        return path, filename, media_type

    # ── Delete ────────────────────────────────────────────────────────────

    def delete_report(self, report_id: str, owner_id: str) -> None:
        owner = self._require_owner(owner_id)
        self._require_report(report_id, owner)
        versions = self._repository.list_versions(report_id, owner)
        for version in versions:
            reference = version.get("storage_reference")
            if reference:
                self._safe_delete(reference)
        self._repository.delete_report(report_id, owner)
        logger.info("report.deleted", extra={"report_id": report_id})

    # ── Helpers ───────────────────────────────────────────────────────────

    def _require_report(self, report_id: str, owner: str) -> Dict[str, Any]:
        row = self._repository.get_report(report_id, owner)
        if row is None:
            raise NotFoundError("Report not found.")
        return row

    def _ensure_research_owned(self, research_id: str, owner: str) -> None:
        if self._projects is not None:
            try:
                self._projects.get_research(research_id, owner)
                return
            except NotFoundError:
                pass
        if self._history is not None:
            self._history.progress(research_id, owner)
            return
        raise NotFoundError("Research session not found.")

    def _store_markdown(
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
            logger.exception("report.storage_failed")
            raise FileProcessingError("Could not store the report content.") from exc

    def _safe_delete(self, key: str) -> None:
        try:
            self._storage.delete(key)
        except Exception:  # pragma: no cover - cleanup is best effort
            logger.exception("report.storage_cleanup_failed")

    @staticmethod
    def _require_owner(owner_id: Optional[str]) -> str:
        owner = (owner_id or "").strip()
        if not owner:
            raise AuthenticationError("Authentication required.")
        return owner


__all__ = ["ReportManagementService", "derive_verification_status"]
