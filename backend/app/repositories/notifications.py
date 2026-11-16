"""
app/repositories/notifications.py
=================================
PostgreSQL persistence adapter for user-facing notifications.

Every notification row carries ``owner_id`` (the authenticated user's Supabase
``sub``), so queries are always owner-scoped. Read/unread state is tracked via
the ``read`` boolean and ``read_at`` timestamp.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence

from sqlalchemy import and_, select

from app.config import Settings, get_settings
from app.database.models.domain import User
from app.database.models.notifications import Notification
from app.database.session import get_session_factory
from app.observability import get_logger

logger = get_logger("app.repositories.notifications")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _uuid(value: Any) -> Optional[uuid.UUID]:
    if value is None:
        return None
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


class NotificationRepository:
    """Owner-scoped CRUD for notifications."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self._settings = settings or get_settings()
        self._available = bool(self._settings.database_url)

    @property
    def available(self) -> bool:
        return self._available

    def _factory(self):
        if not self._available:
            raise RuntimeError("PostgreSQL is not configured")
        return get_session_factory(self._settings)

    # ── Create ─────────────────────────────────────────────────────────

    def create_notification(
        self,
        *,
        owner_id: str,
        category: str,
        title: str,
        body: Optional[str] = None,
        data: Optional[Mapping[str, Any]] = None,
    ) -> Optional[dict[str, Any]]:
        """Insert one notification row for *owner_id*.

        Returns the persisted row dict, or ``None`` when PostgreSQL is
        unavailable.
        """
        owner_uuid = _uuid(owner_id)
        if owner_uuid is None:
            return None

        import json

        data_json = None
        if data is not None:
            try:
                data_json = json.dumps(data, default=str)
            except (TypeError, ValueError):
                data_json = None

        db = self._factory()()
        try:
            row = Notification(
                owner_id=owner_uuid,
                category=category[:40],
                title=title[:200],
                body=body,
                data_json=data_json,
                read=False,
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            return self._row_to_dict(row)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    # ── Read ───────────────────────────────────────────────────────────

    def get_notification(
        self, *, notification_id: str, owner_id: str
    ) -> Optional[dict[str, Any]]:
        """Return one notification only if owned by *owner_id*."""
        nid = _uuid(notification_id)
        owner_uuid = _uuid(owner_id)
        if nid is None or owner_uuid is None:
            return None

        db = self._factory()()
        try:
            row = db.get(Notification, nid)
            if row is None or row.owner_id != owner_uuid:
                return None
            return self._row_to_dict(row)
        finally:
            db.close()

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
        """List notifications owned by *owner_id*, newest first by default.

        Filters are combined with AND. ``read`` filters on read/unread state;
        ``category`` filters on notification type.
        """
        owner_uuid = _uuid(owner_id)
        if owner_uuid is None:
            return [], 0

        db = self._factory()()
        try:
            stmt = select(Notification).where(
                Notification.owner_id == owner_uuid,
            )

            if read is not None:
                stmt = stmt.where(Notification.read == read)
            if category:
                stmt = stmt.where(Notification.category == category)

            column = getattr(Notification, sort, Notification.created_at)
            direction = (
                column.asc() if order.lower() == "asc" else column.desc()
            )
            stmt = stmt.order_by(direction, Notification.id.desc())

            total_stmt = select(Notification.id).where(stmt.whereclause)  # type: ignore[arg-type]
            total = len(db.scalars(total_stmt).all())

            rows = db.scalars(stmt.limit(limit).offset(offset)).all()

            return [self._row_to_dict(row) for row in rows], total
        finally:
            db.close()

    def count_unread(self, *, owner_id: str) -> int:
        """Return the number of unread notifications for *owner_id*."""
        owner_uuid = _uuid(owner_id)
        if owner_uuid is None:
            return 0

        db = self._factory()()
        try:
            rows = db.scalars(
                select(Notification.id).where(
                    and_(
                        Notification.owner_id == owner_uuid,
                        Notification.read.is_(False),
                    )
                )
            ).all()
            return len(rows)
        finally:
            db.close()

    # ── Update ─────────────────────────────────────────────────────────

    def mark_read(
        self, *, notification_id: str, owner_id: str
    ) -> Optional[dict[str, Any]]:
        """Mark one owned notification as read. Returns the updated row."""
        nid = _uuid(notification_id)
        owner_uuid = _uuid(owner_id)
        if nid is None or owner_uuid is None:
            return None

        db = self._factory()()
        try:
            row = db.get(Notification, nid)
            if row is None or row.owner_id != owner_uuid:
                return None
            if not row.read:
                row.read = True
                row.read_at = _utcnow()
                db.commit()
                db.refresh(row)
            return self._row_to_dict(row)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def mark_all_read(self, *, owner_id: str) -> int:
        """Mark all unread notifications owned by *owner_id* as read.

        Returns the number of rows updated.
        """
        owner_uuid = _uuid(owner_id)
        if owner_uuid is None:
            return 0

        db = self._factory()()
        try:
            rows = db.scalars(
                select(Notification).where(
                    and_(
                        Notification.owner_id == owner_uuid,
                        Notification.read.is_(False),
                    )
                )
            ).all()
            count = 0
            now = _utcnow()
            for row in rows:
                row.read = True
                row.read_at = now
                count += 1
            if count:
                db.commit()
            return count
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    # ── Delete ─────────────────────────────────────────────────────────

    def delete_notification(
        self, *, notification_id: str, owner_id: str
    ) -> bool:
        """Soft-delete one owned notification. Returns ``True`` if deleted."""
        nid = _uuid(notification_id)
        owner_uuid = _uuid(owner_id)
        if nid is None or owner_uuid is None:
            return False

        db = self._factory()()
        try:
            row = db.get(Notification, nid)
            if row is None or row.owner_id != owner_uuid:
                return False
            db.delete(row)
            db.commit()
            return True
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    # ── Helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _row_to_dict(row: Notification) -> dict[str, Any]:
        import json

        data = None
        if row.data_json:
            try:
                data = json.loads(row.data_json)
            except (TypeError, ValueError):
                data = None
        return {
            "notification_id": str(row.id),
            "owner_id": str(row.owner_id),
            "category": row.category,
            "title": row.title,
            "body": row.body,
            "data": data,
            "read": row.read,
            "read_at": row.read_at.isoformat() if row.read_at else None,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }


class NullNotificationRepository:
    """No-op adapter for local/test runs without PostgreSQL."""

    available = False

    def create_notification(self, **_: Any) -> Optional[dict[str, Any]]:
        return None

    def get_notification(self, **_: Any) -> Optional[dict[str, Any]]:
        return None

    def list_notifications(self, **_: Any) -> tuple[list[dict[str, Any]], int]:
        return [], 0

    def count_unread(self, **_: Any) -> int:
        return 0

    def mark_read(self, **_: Any) -> Optional[dict[str, Any]]:
        return None

    def mark_all_read(self, **_: Any) -> int:
        return 0

    def delete_notification(self, **_: Any) -> bool:
        return False


def build_notification_repository(
    settings: Optional[Settings] = None,
) -> NotificationRepository | NullNotificationRepository:
    cfg = settings or get_settings()
    if cfg.database_url:
        return NotificationRepository(cfg)
    return NullNotificationRepository()


__all__ = [
    "NotificationRepository",
    "NullNotificationRepository",
    "build_notification_repository",
]
