"""
tests/test_notifications_api.py
================================
Tests for the research notification system (Task 4.7).

Covers
------
* GET /notifications — list with pagination, read/category filters, sorting
* GET /notifications/unread — count unread notifications
* PATCH /notifications/{id}/read — mark one as read
* POST /notifications/read-all — mark all as read
* DELETE /notifications/{id} — delete one
* Invalid notification IDs (422)
* Unauthenticated access (401)
* Cross-user access (404)
* Read/unread status transitions
* All notification categories
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.factory import create_app
from app.schemas.enums import NotificationStatus, NotificationType
from app.security.dependencies import get_current_user
from app.services.container import get_notification_service
from app.services.notification_service import NotificationService


# ─── Fake repository for API tests ─────────────────────────────────────


class FakeNotificationRepository:
    """In-memory double of NotificationRepository for API tests."""

    def __init__(self) -> None:
        self._notifications: Dict[str, Dict[str, Any]] = {}
        self.available = True

    def create_notification(self, *, owner_id, category, title,
                            body=None, data=None):
        import uuid as _uuid
        nid = str(_uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        row = {
            "notification_id": nid,
            "owner_id": owner_id,
            "category": category,
            "title": title,
            "body": body,
            "data": data,
            "read": False,
            "read_at": None,
            "created_at": now,
            "updated_at": now,
        }
        self._notifications[nid] = row
        return row

    def get_notification(self, *, notification_id, owner_id):
        row = self._notifications.get(notification_id)
        if row is None or row["owner_id"] != owner_id:
            return None
        return row

    def list_notifications(self, *, owner_id, read=None, category=None,
                           sort="created_at", order="desc", limit=20, offset=0):
        rows = [
            r for r in self._notifications.values()
            if r["owner_id"] == owner_id
        ]
        if read is not None:
            rows = [r for r in rows if r["read"] == read]
        if category:
            rows = [r for r in rows if r["category"] == category]
        reverse = order == "desc"
        rows.sort(key=lambda r: r.get(sort, r["created_at"]), reverse=reverse)
        total = len(rows)
        page = rows[offset:offset + limit]
        return page, total

    def count_unread(self, *, owner_id):
        return sum(
            1 for r in self._notifications.values()
            if r["owner_id"] == owner_id and not r["read"]
        )

    def mark_read(self, *, notification_id, owner_id):
        row = self._notifications.get(notification_id)
        if row is None or row["owner_id"] != owner_id:
            return None
        if not row["read"]:
            row["read"] = True
            row["read_at"] = datetime.now(timezone.utc).isoformat()
            row["updated_at"] = row["read_at"]
        return row

    def mark_all_read(self, *, owner_id):
        count = 0
        now = datetime.now(timezone.utc).isoformat()
        for row in self._notifications.values():
            if row["owner_id"] == owner_id and not row["read"]:
                row["read"] = True
                row["read_at"] = now
                row["updated_at"] = now
                count += 1
        return count

    def delete_notification(self, *, notification_id, owner_id):
        row = self._notifications.get(notification_id)
        if row is None or row["owner_id"] != owner_id:
            return False
        del self._notifications[notification_id]
        return True


def _make_service() -> NotificationService:
    service = NotificationService.__new__(NotificationService)
    service._settings = MagicMock()
    service._repo = FakeNotificationRepository()
    from app.infrastructure.local_events import InProcessEventBus
    service._event_bus = InProcessEventBus()
    return service


@pytest.fixture
def user_state() -> dict:
    return {"user_id": "user-a"}


def _build_client(user_state: dict, service: NotificationService) -> TestClient:
    application = create_app(get_settings())
    application.dependency_overrides[get_current_user] = lambda: dict(user_state)
    application.dependency_overrides[get_notification_service] = lambda: service
    return TestClient(application, raise_server_exceptions=False)


@pytest.fixture
def env(user_state):
    service = _make_service()
    client = _build_client(user_state, service)
    return service, client


# ── List notifications ─────────────────────────────────────────────────


class TestListNotifications:
    def test_list_returns_created_notifications(self, env):
        service, client = env
        service.create_notification(
            owner_id="user-a",
            category=NotificationType.RESEARCH_QUEUED.value,
            title="Research queued",
            body="Your research on AI has been queued.",
        )
        service.create_notification(
            owner_id="user-a",
            category=NotificationType.RESEARCH_COMPLETED.value,
            title="Research completed",
            body="Your research on AI is complete.",
        )

        response = client.get("/notifications")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 2
        assert len(body["notifications"]) == 2
        titles = {n["title"] for n in body["notifications"]}
        assert titles == {"Research queued", "Research completed"}

    def test_list_pagination(self, env):
        service, client = env
        for i in range(5):
            service.create_notification(
                owner_id="user-a",
                category=NotificationType.RESEARCH_COMPLETED.value,
                title=f"Notification {i}",
                body=f"Body {i}",
            )
        response = client.get("/notifications?limit=2&offset=1")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 5
        assert len(body["notifications"]) == 2
        assert body["limit"] == 2
        assert body["offset"] == 1

    def test_list_filter_by_read(self, env):
        service, client = env
        service.create_notification(
            owner_id="user-a",
            category=NotificationType.RESEARCH_COMPLETED.value,
            title="Unread notification",
            body="Not read yet.",
        )
        row = service.create_notification(
            owner_id="user-a",
            category=NotificationType.RESEARCH_COMPLETED.value,
            title="Read notification",
            body="Already read.",
        )
        service.mark_read(notification_id=row["notification_id"], owner_id="user-a")

        response = client.get("/notifications?read=true")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["notifications"][0]["title"] == "Read notification"

        response = client.get("/notifications?read=false")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["notifications"][0]["title"] == "Unread notification"

    def test_list_filter_by_category(self, env):
        service, client = env
        service.create_notification(
            owner_id="user-a",
            category=NotificationType.RESEARCH_COMPLETED.value,
            title="Research done",
            body="Done.",
        )
        service.create_notification(
            owner_id="user-a",
            category=NotificationType.RESEARCH_FAILED.value,
            title="Research failed",
            body="Failed.",
        )
        response = client.get(
            f"/notifications?category={NotificationType.RESEARCH_FAILED.value}"
        )
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["notifications"][0]["category"] == "research_failed"

    def test_list_sort_ascending(self, env):
        service, client = env
        service.create_notification(
            owner_id="user-a",
            category=NotificationType.RESEARCH_COMPLETED.value,
            title="A",
            body="A",
        )
        service.create_notification(
            owner_id="user-a",
            category=NotificationType.RESEARCH_COMPLETED.value,
            title="B",
            body="B",
        )
        response = client.get("/notifications?sort=title&order=asc")
        assert response.status_code == 200
        body = response.json()
        assert [n["title"] for n in body["notifications"]] == ["A", "B"]


# ── Unread count ───────────────────────────────────────────────────────


class TestUnreadCount:
    def test_count_unread(self, env):
        service, client = env
        service.create_notification(
            owner_id="user-a",
            category=NotificationType.RESEARCH_COMPLETED.value,
            title="Unread 1",
            body="Unread 1.",
        )
        service.create_notification(
            owner_id="user-a",
            category=NotificationType.RESEARCH_COMPLETED.value,
            title="Unread 2",
            body="Unread 2.",
        )
        row = service.create_notification(
            owner_id="user-a",
            category=NotificationType.RESEARCH_COMPLETED.value,
            title="Read",
            body="Read.",
        )
        service.mark_read(notification_id=row["notification_id"], owner_id="user-a")

        response = client.get("/notifications/unread")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["unread_count"] == 2

    def test_count_unread_all_read(self, env):
        service, client = env
        service.create_notification(
            owner_id="user-a",
            category=NotificationType.RESEARCH_COMPLETED.value,
            title="N1",
            body="N1.",
        )
        service.create_notification(
            owner_id="user-a",
            category=NotificationType.RESEARCH_COMPLETED.value,
            title="N2",
            body="N2.",
        )
        response = client.post("/notifications/read-all")
        assert response.status_code == 200
        body = response.json()
        assert body["marked_read"] == 2

        response = client.get("/notifications/unread")
        assert response.status_code == 200
        body = response.json()
        assert body["unread_count"] == 0


# ── Mark one as read ───────────────────────────────────────────────────


class TestMarkRead:
    def test_mark_read(self, env):
        service, client = env
        row = service.create_notification(
            owner_id="user-a",
            category=NotificationType.RESEARCH_COMPLETED.value,
            title="To Read",
            body="To read.",
        )
        assert row["read"] is False

        response = client.patch(f"/notifications/{row['notification_id']}/read")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["notification"]["read"] is True
        assert body["notification"]["read_at"] is not None

    def test_mark_read_unknown_is_404(self, env):
        _, client = env
        import uuid as _uuid
        response = client.patch(f"/notifications/{_uuid.uuid4()}/read")
        assert response.status_code == 404, response.text

    def test_mark_read_malformed_id_is_422(self, env):
        _, client = env
        response = client.patch("/notifications/not-a-uuid/read")
        assert response.status_code == 422, response.text


# ── Mark all as read ───────────────────────────────────────────────────


class TestMarkAllRead:
    def test_mark_all_read(self, env):
        service, client = env
        service.create_notification(
            owner_id="user-a",
            category=NotificationType.RESEARCH_COMPLETED.value,
            title="N1",
            body="N1.",
        )
        service.create_notification(
            owner_id="user-a",
            category=NotificationType.RESEARCH_COMPLETED.value,
            title="N2",
            body="N2.",
        )

        response = client.post("/notifications/read-all")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["marked_read"] == 2

        response = client.get("/notifications/unread")
        assert response.status_code == 200
        body = response.json()
        assert body["unread_count"] == 0


# ── Delete ─────────────────────────────────────────────────────────────


class TestDelete:
    def test_delete_notification(self, env):
        service, client = env
        row = service.create_notification(
            owner_id="user-a",
            category=NotificationType.RESEARCH_COMPLETED.value,
            title="Delete Me",
            body="Delete me.",
        )
        response = client.delete(f"/notifications/{row['notification_id']}")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "deleted"
        assert body["notification_id"] == row["notification_id"]

        # Verify it's gone from the list
        response = client.get("/notifications")
        assert response.status_code == 200
        body = response.json()
        ids = {n["notification_id"] for n in body["notifications"]}
        assert row["notification_id"] not in ids

    def test_delete_unknown_is_404(self, env):
        _, client = env
        import uuid as _uuid
        response = client.delete(f"/notifications/{_uuid.uuid4()}")
        assert response.status_code == 404, response.text


# ── Authentication ─────────────────────────────────────────────────────


class TestAuthentication:
    def test_all_endpoints_require_auth(self):
        application = create_app(get_settings())
        import uuid as _uuid
        with TestClient(application, raise_server_exceptions=False) as tc:
            assert tc.get("/notifications").status_code == 401
            assert tc.get("/notifications/unread").status_code == 401
            assert tc.patch(
                f"/notifications/{_uuid.uuid4()}/read"
            ).status_code == 401
            assert tc.post("/notifications/read-all").status_code == 401
            assert tc.delete(f"/notifications/{_uuid.uuid4()}").status_code == 401


# ── Cross-user isolation ──────────────────────────────────────────────


class TestCrossUserIsolation:
    def test_cannot_access_another_users_notification(self, env):
        service, client = env
        row = service.create_notification(
            owner_id="user-b",  # different owner
            category=NotificationType.RESEARCH_COMPLETED.value,
            title="Private Notification",
            body="Private.",
        )
        response = client.get("/notifications")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 0  # user-a sees none of user-b's

        response = client.patch(
            f"/notifications/{row['notification_id']}/read"
        )
        assert response.status_code == 404, response.text

        response = client.delete(f"/notifications/{row['notification_id']}")
        assert response.status_code == 404, response.text

    def test_list_hides_other_users_notifications(self, env):
        service, client = env
        service.create_notification(
            owner_id="user-b",
            category=NotificationType.RESEARCH_COMPLETED.value,
            title="Private",
            body="Private.",
        )
        service.create_notification(
            owner_id="user-a",
            category=NotificationType.RESEARCH_COMPLETED.value,
            title="Public",
            body="Public.",
        )
        response = client.get("/notifications")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["notifications"][0]["title"] == "Public"


# ── All notification categories ──────────────────────────────────────


class TestCategories:
    def test_all_categories_can_be_created(self, env):
        service, client = env
        for category in NotificationType:
            row = service.create_notification(
                owner_id="user-a",
                category=category.value,
                title=f"Test {category.value}",
                body=f"Body for {category.value}.",
            )
            assert row is not None
            assert row["category"] == category.value

        response = client.get("/notifications")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == len(NotificationType)


# ── Notification lifecycle convenience helpers ─────────────────────────


class TestLifecycleHelpers:
    def test_research_queued(self, env):
        service, client = env
        row = service.notify_research_queued(
            owner_id="user-a",
            research_id="research-123",
            topic="AI in healthcare",
        )
        assert row is not None
        assert row["category"] == "research_queued"
        assert "queued" in row["title"].lower()
        assert row["data"]["research_id"] == "research-123"
        assert row["data"]["topic"] == "AI in healthcare"

    def test_research_started(self, env):
        service, client = env
        row = service.notify_research_started(
            owner_id="user-a",
            research_id="research-123",
            topic="AI in healthcare",
        )
        assert row["category"] == "research_started"

    def test_research_completed(self, env):
        service, client = env
        row = service.notify_research_completed(
            owner_id="user-a",
            research_id="research-123",
            topic="AI in healthcare",
        )
        assert row["category"] == "research_completed"

    def test_research_failed(self, env):
        service, client = env
        row = service.notify_research_failed(
            owner_id="user-a",
            research_id="research-123",
            topic="AI in healthcare",
            error="Timeout",
        )
        assert row["category"] == "research_failed"
        assert "Timeout" in row["body"]

    def test_approval_required(self, env):
        service, client = env
        row = service.notify_approval_required(
            owner_id="user-a",
            research_id="research-123",
            topic="AI in healthcare",
        )
        assert row["category"] == "approval_required"

    def test_revision_requested(self, env):
        service, client = env
        row = service.notify_revision_requested(
            owner_id="user-a",
            research_id="research-123",
            topic="AI in healthcare",
        )
        assert row["category"] == "revision_requested"

    def test_report_approved(self, env):
        service, client = env
        row = service.notify_report_approved(
            owner_id="user-a",
            research_id="research-123",
            topic="AI in healthcare",
        )
        assert row["category"] == "report_approved"

    def test_document_processing_completed(self, env):
        service, client = env
        row = service.notify_document_processing_completed(
            owner_id="user-a",
            document_id="doc-123",
            filename="paper.pdf",
        )
        assert row["category"] == "document_processing_completed"
        assert "paper.pdf" in row["body"]

    def test_document_processing_failed(self, env):
        service, client = env
        row = service.notify_document_processing_failed(
            owner_id="user-a",
            document_id="doc-123",
            filename="paper.pdf",
            error="Corrupt file",
        )
        assert row["category"] == "document_processing_failed"
        assert "Corrupt file" in row["body"]


# ── Read/unread status transitions ─────────────────────────────────────


class TestReadUnreadTransitions:
    def test_new_notification_is_unread(self, env):
        service, client = env
        row = service.create_notification(
            owner_id="user-a",
            category=NotificationType.RESEARCH_COMPLETED.value,
            title="New",
            body="New.",
        )
        assert row["read"] is False
        assert row["read_at"] is None

    def test_mark_read_sets_read_at(self, env):
        service, client = env
        row = service.create_notification(
            owner_id="user-a",
            category=NotificationType.RESEARCH_COMPLETED.value,
            title="To Read",
            body="To read.",
        )
        updated = service.mark_read(
            notification_id=row["notification_id"], owner_id="user-a"
        )
        assert updated["read"] is True
        assert updated["read_at"] is not None

    def test_mark_all_read_sets_all(self, env):
        service, client = env
        service.create_notification(
            owner_id="user-a",
            category=NotificationType.RESEARCH_COMPLETED.value,
            title="N1",
            body="N1.",
        )
        service.create_notification(
            owner_id="user-a",
            category=NotificationType.RESEARCH_COMPLETED.value,
            title="N2",
            body="N2.",
        )
        count = service.mark_all_read(owner_id="user-a")
        assert count == 2

        response = client.get("/notifications/unread")
        assert response.status_code == 200
        body = response.json()
        assert body["unread_count"] == 0


__all__ = [
    "TestListNotifications",
    "TestUnreadCount",
    "TestMarkRead",
    "TestMarkAllRead",
    "TestDelete",
    "TestAuthentication",
    "TestCrossUserIsolation",
    "TestCategories",
    "TestLifecycleHelpers",
    "TestReadUnreadTransitions",
]
