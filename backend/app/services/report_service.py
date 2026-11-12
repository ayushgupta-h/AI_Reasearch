"""
app/services/report_service.py
==============================
Report artifact access, sharing and export.

All file-system policy lives here — path traversal protection, media types,
ownership checks and export naming — so the HTTP layer only builds responses.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from app.config import Settings
from app.errors import (
    AuthenticationError,
    AuthorizationError,
    BadRequestError,
    FileProcessingError,
    NotFoundError,
)
from app.observability import get_logger
from app.schemas.requests import ExportRequest
from app.security.share_tokens import generate_share_token, verify_share_token
from app.services.session_service import SessionService

logger = get_logger("app.services.reports")

MEDIA_TYPES: Dict[str, str] = {
    ".md": "text/markdown; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
DEFAULT_MEDIA_TYPE = "application/octet-stream"
FORMAT_EXTENSIONS = {"pdf": ".pdf", "docx": ".docx", "md": ".md", "txt": ".txt"}
# Only report artifacts may be served; anything else in the directory stays private.
ALLOWED_EXTENSIONS = frozenset(MEDIA_TYPES)
MAX_FILENAME_LENGTH = 255


def _slugify_topic(topic: Optional[str]) -> str:
    raw = (topic or "research_report").strip()
    slug = re.sub(r"[^\w\s-]", "", raw).strip().lower()
    slug = re.sub(r"[-\s]+", "_", slug)[:60].strip("_")
    return slug or "research_report"


class ReportService:
    def __init__(self, settings: Settings, sessions: SessionService) -> None:
        self._settings = settings
        self._sessions = sessions

    # ── Resolution ────────────────────────────────────────────────────────

    def resolve(self, filename: str) -> Path:
        """Resolve a report file, rejecting traversal, symlinks and foreign types.

        User-supplied names are never trusted directly: separators are
        normalised, ``.``/``..`` and hidden segments are rejected, only known
        report extensions are served, and the resolved path must stay inside
        the reports directory with no symlink hops.
        """
        if not isinstance(filename, str) or not filename.strip():
            raise BadRequestError("Invalid report filename.")
        if len(filename) > MAX_FILENAME_LENGTH:
            raise BadRequestError("Invalid report filename.")
        if "\x00" in filename or any(ord(char) < 32 for char in filename):
            raise BadRequestError("Invalid report filename.")

        parts = [part for part in filename.replace("\\", "/").split("/") if part not in ("", ".")]
        if not parts or any(part == ".." for part in parts):
            raise AuthorizationError("Access denied: invalid report path.")
        if any(part.startswith(".") for part in parts):
            raise BadRequestError("Invalid report filename.")
        if Path(parts[-1]).suffix.lower() not in ALLOWED_EXTENSIONS:
            raise NotFoundError("Report file not found.")

        base = self._settings.reports_dir.resolve()
        raw = base.joinpath(*parts)
        if raw.is_symlink():
            logger.warning(
                "report.path_symlink_rejected", extra={"error_code": "forbidden"}
            )
            raise AuthorizationError("Access denied: invalid report path.")
        candidate = raw.resolve()
        if candidate == base or base not in candidate.parents:
            raise AuthorizationError("Access denied: invalid report path.")
        if not candidate.is_file():
            raise NotFoundError("Report file not found.")
        return candidate

    @staticmethod
    def media_type_for(path: Path) -> str:
        return MEDIA_TYPES.get(path.suffix.lower(), DEFAULT_MEDIA_TYPE)

    # ── Sharing ───────────────────────────────────────────────────────────

    def share_link(self, filename: str, user: Optional[Dict[str, Any]]) -> Dict[str, str]:
        """Mint a read-only share link. Only the report owner may create one."""
        if not user or not user.get("user_id"):
            raise AuthenticationError("Sign in to create a share link for a report.")
        clean = Path(filename).name
        self.resolve(filename)
        if not self._sessions.user_owns_report(user["user_id"], clean):
            logger.warning(
                "report.share_link_denied",
                extra={"report_filename": clean, "error_code": "forbidden"},
            )
            raise AuthorizationError("Access denied: You do not own this research report.")
        token = generate_share_token(clean)
        logger.info("report.share_link_created", extra={"report_filename": clean})
        return {
            "filename": clean,
            "share_token": token,
            "share_url": f"/report.html?run={clean}&share={token}",
        }

    # ── Download authorisation ────────────────────────────────────────────

    def authorize_download(
        self,
        filename: str,
        *,
        user: Optional[Dict[str, Any]],
        share_token: Optional[str],
    ) -> Tuple[Path, str, bool]:
        """Return ``(path, media_type, read_only)`` for an authorised download."""
        path = self.resolve(filename)
        name = path.name

        if share_token and verify_share_token(name, share_token):
            logger.info(
                "report.download_authorized",
                extra={"report_filename": name, "read_only": True},
            )
            return path, self.media_type_for(path), True

        if not user or not user.get("user_id"):
            raise AuthenticationError("Authentication required to view this report.")
        if not self._sessions.user_owns_report(user["user_id"], name):
            logger.warning(
                "report.download_denied",
                extra={"report_filename": name, "error_code": "forbidden"},
            )
            raise AuthorizationError("Access denied: You do not own this research report.")
        logger.info(
            "report.download_authorized",
            extra={"report_filename": name, "read_only": False},
        )
        return path, self.media_type_for(path), False

    # ── Export ────────────────────────────────────────────────────────────

    def export(self, request: ExportRequest) -> Tuple[Path, str, str]:
        """Render markdown into a downloadable artifact (blocking call)."""
        content = (request.content or "").strip()
        if not content:
            raise BadRequestError("Cannot export an empty report draft.")

        fmt = request.format
        extension = FORMAT_EXTENSIONS.get(fmt)
        if extension is None:  # pragma: no cover - schema already restricts this
            raise BadRequestError(f"Unsupported export format '{fmt}'.")

        filename = f"quest_{_slugify_topic(request.topic)}_{time.strftime('%Y%m%d_%H%M%S')}{extension}"
        target = self._settings.exports_dir / filename
        target.parent.mkdir(parents=True, exist_ok=True)

        try:
            started = time.perf_counter()
            from report_exporter import ReportExporter

            ReportExporter().export_single(
                markdown_text=content,
                fmt=fmt,
                output_path=target,
                citation_style=request.citation_style,
            )
            from app.telemetry import get_metrics

            get_metrics().record_report_generation(time.perf_counter() - started)
        except Exception as exc:
            logger.exception("report.export_failed", extra={"error_code": "file_processing_failed"})
            raise FileProcessingError(
                f"Failed to export the report as {fmt.upper()}."
            ) from exc

        logger.info("report.exported", extra={"format": fmt, "report_filename": filename})
        return target, filename, self.media_type_for(target)
