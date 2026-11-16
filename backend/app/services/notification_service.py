"""
app/services/notification_service.py
====================================
User-facing notification service.

Persists notifications to PostgreSQL and (optionally) publishes them to the
existing event bus for live delivery over Redis Streams / SSE. No email, SMS
or push notification services are introduced unless explicitly configured.

Notification categories:
  * research_queued
  * research_started
  * research_completed
  * research_failed
  * approval_required
  * revision_requested
  * report_approved
  * document_processing_completed
  * document_processing_failed
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Optional

from app.config import Settings, get_settings
from app.errors import AuthenticationError, NotFoundError
from app.infrastructure.ports import EventBus
from app.infrastructure.local_events import InProcessEventBus
from app.observability import get_logger
from app.repositories.notifications import (
    NullNotificationRepository,
    build_notification_repository,
)
from app.schemas.enums import NotificationStatus, NotificationType

logger = get_logger("app.services.notifications")


def _require_owner(owner_id: Optional[str]) -> str:
    owner = (owner_id or "").strip()
    if not owner:
        raise AuthenticationError("Authentication required.")
    return owner


class NotificationService:
    """Business logic for user-facing notifications."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        event_bus: Optional[EventBus] = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._repo = build_notification_repository(self._settings)
        # Reuse the existing in-process event bus (or an injected Redis-backed
        # one) for live delivery. Falls back to a fresh bus when none is given.
        self._event_bus = event_bus or InProcessEventBus()

    @property
    def available(self) -> bool:
        return self._repo.available

    # ── Create ─────────────────────────────────────────────────────────

    def create_notification(
        self,
        *,
        owner_id: str,
        category: str,
        title: str,
        body: Optional[str] = None,
        data: Optional[Mapping[str, Any]] = None,
        publish_live: bool = True,
    ) -> Optional[dict[str, Any]]:
        """Persist one notification and optionally publish it live.

        ``publish_live`` publishes the notification to the event bus under the
        ``notification.{category}`` topic so an SSE / Redis Streams consumer
        can push it to the client in real time. Delivery failures are logged
        but never block persistence.
        """
        _require_owner(owner_id)
        row = self._repo.create_notification(
            owner_id=owner_id,
            category=category,
            title=title,
            body=body,
            data=data,
        )
        if row is None:
            return None

        if publish_live and self._event_bus:
            try:
                self._event_bus.publish(
                    f"notification.{category}",
                    {
                        "notification_id": row["notification_id"],
                        "owner_id": row["owner_id"],
                        "category": row["category"],
                        "title": row["title"],
                        "body": row["body"],
                        "data": row.get("data"),
                        "read": row["read"],
                        "created_at": row["created_at"],
                    },
                )
            except Exception:  # live delivery must never block persistence
                logger.exception(
                    "notification.live_publish_failed",
                    extra={
                        "notification_id": row["notification_id"],
                        "category": category,
                    },
                )

        return row

    # ── Read ───────────────────────────────────────────────────────────

    def get_notification(
        self, *, notification_id: str, owner_id: str
    ) -> Optional[dict[str, Any]]:
        """Return one notification if owned by *owner_id*."""
        _require_owner(owner_id)
        row = self._repo.get_notification(
            notification_id=notification_id, owner_id=owner_id
        )
        if row is None:
            raise NotFoundError("Notification not found.")
        return row

    def list_notifications(
        self,
        *,
        owner_id: str,
        read: Optional[bool] = None,
        category: Optional[str] = None,
        sort: str = "created_at",
        order: str = "desc",
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """List owned notifications with optional filters."""
        _require_owner(owner_id)
        return self._repo.list_notifications(
            owner_id=owner_id,
            read=read,
            category=category,
            sort=sort,
            order=order,
            limit=limit,
            offset=offset,
        )

    def count_unread(self, *, owner_id: str) -> int:
        """Return the number of unread notifications for *owner_id*."""
        _require_owner(owner_id)
        return self._repo.count_unread(owner_id=owner_id)

    # ── Update ─────────────────────────────────────────────────────────

    def mark_read(
        self, *, notification_id: str, owner_id: str
    ) -> dict[str, Any]:
        """Mark one owned notification as read."""
        _require_owner(owner_id)
        row = self._repo.mark_read(
            notification_id=notification_id, owner_id=owner_id
        )
        if row is None:
            raise NotFoundError("Notification not found.")
        return row

    def mark_all_read(self, *, owner_id: str) -> int:
        """Mark all unread notifications owned by *owner_id* as read."""
        _require_owner(owner_id)
        return self._repo.mark_all_read(owner_id=owner_id)

    # ── Delete ─────────────────────────────────────────────────────────

    def delete_notification(
        self, *, notification_id: str, owner_id: str
    ) -> None:
        """Soft-delete one owned notification."""
        _require_owner(owner_id)
        ok = self._repo.delete_notification(
            notification_id=notification_id, owner_id=owner_id
        )
        if not ok:
            raise NotFoundError("Notification not found.")

    # ── Convenience helpers for research lifecycle events ──────────────

    def notify_research_queued(
        self, *, owner_id: str, research_id: str, topic: str
    ) -> Optional[dict[str, Any]]:
        return self.create_notification(
            owner_id=owner_id,
            category=NotificationType.RESEARCH_QUEUED.value,
            title="Research queued",
            body=f"Your research on “{topic}” has been queued.",
            data={"research_id": research_id, "topic": topic},
        )

    def notify_research_started(
        self, *, owner_id: str, research_id: str, topic: str
    ) -> Optional[dict[str, Any]]:
        return self.create_notification(
            owner_id=owner_id,
            category=NotificationType.RESEARCH_STARTED.value,
            title="Research started",
            body=f"Research on “{topic}” is now running.",
            data={"research_id": research_id, "topic": topic},
        )

    def notify_research_completed(
        self, *, owner_id: str, research_id: str, topic: str
    ) -> Optional[dict[str, Any]]:
        return self.create_notification(
            owner_id=owner_id,
            category=NotificationType.RESEARCH_COMPLETED.value,
            title="Research completed",
            body=f"Research on “{topic}” is complete.",
            data={"research_id": research_id, "topic": topic},
        )

    def notify_research_failed(
        self, *, owner_id: str, research_id: str, topic: str, error: str
    ) -> Optional[dict[str, Any]]:
        return self.create_notification(
            owner_id=owner_id,
            category=NotificationType.RESEARCH_FAILED.value,
            title="Research failed",
            body=f"Research on “{topic}” failed: {error}.",
            data={"research_id": research_id, "topic": topic, "error": error},
        )

    def notify_approval_required(
        self, *, owner_id: str, research_id: str, topic: str
    ) -> Optional[dict[str, Any]]:
        return self.create_notification(
            owner_id=owner_id,
            category=NotificationType.APPROVAL_REQUIRED.value,
            title="Approval required",
            body=f"Research on “{topic}” needs your approval to proceed.",
            data={"research_id": research_id, "topic": topic},
        )

    def notify_revision_requested(
        self, *, owner_id: str, research_id: str, topic: str
    ) -> Optional[dict[str, Any]]:
        return self.create_notification(
            owner_id=owner_id,
            category=NotificationType.REVISION_REQUESTED.value,
            title="Revision requested",
            body=f"Revisions requested for research on “{topic}”.",
            data={"research_id": research_id, "topic": topic},
        )

    def notify_report_approved(
        self, *, owner_id: str, research_id: str, topic: str
    ) -> Optional[dict[str, Any]]:
        return self.create_notification(
            owner_id=owner_id,
            category=NotificationType.REPORT_APPROVED.value,
            title="Report approved",
            body=f"The report for “{topic}” has been approved.",
            data={"research_id": research_id, "topic": topic},
        )

    def notify_document_processing_completed(
        self, *, owner_id: str, document_id: str, filename: str
    ) -> Optional[dict[str, Any]]:
        return self.create_notification(
            owner_id=owner_id,
            category=NotificationType.DOCUMENT_PROCESSING_COMPLETED.value,
            title="Document processing completed",
            body=f"“{filename}” has been processed and indexed.",
            data={"document_id": document_id, "filename": filename},
        )

    def notify_document_processing_failed(
        self, *, owner_id: str, document_id: str, filename: str, error: str
    ) -> Optional[dict[str, Any]]:
        return self.create_notification(
            owner_id=owner_id,
            category=NotificationType.DOCUMENT_PROCESSING_FAILED.value,
            title="Document processing failed",
            body=f"Processing of “{filename}” failed: {error}.",
            data={"document_id": document_id, "filename": filename, "error": error},
        )


__all__ = ["NotificationService"]
