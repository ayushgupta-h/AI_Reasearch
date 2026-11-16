"""
app/api/notifications.py
========================
User-facing notification API.

Endpoints
---------
GET    /notifications               list notifications (pagination + filters)
GET    /notifications/unread        count unread notifications
PATCH  /notifications/{id}/read     mark one notification as read
POST   /notifications/read-all      mark all notifications as read
DELETE /notifications/{id}          delete one notification

All endpoints are user-scoped: a caller can only touch their own notifications.
The authenticated JWT identity is the sole source of ``owner_id`` — it is never
trusted from the request body.
"""

from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Body, Depends, Path, Query

from app.errors import AuthenticationError, NotFoundError
from app.schemas.enums import NotificationStatus, NotificationType
from app.schemas.errors import ApiError
from app.security.dependencies import get_current_user
from app.services.container import get_notification_service
from app.services.notification_service import NotificationService

router = APIRouter(tags=["Notifications"])

NotificationIdPath = Path(
    ...,
    pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    description="UUID notification id.",
)

_ERRORS = {
    401: {"model": ApiError, "description": "Authentication required."},
    404: {"model": ApiError, "description": "Notification not found."},
    422: {"model": ApiError, "description": "Invalid field value or ID format."},
}


def _owner(current_user: dict) -> str:
    owner = str(current_user.get("user_id") or "").strip()
    if not owner:
        raise AuthenticationError("Authentication required.")
    return owner


@router.get(
    "/notifications",
    response_model=dict,
    responses=_ERRORS,
    summary="List notifications",
)
async def list_notifications(
    read: Optional[bool] = Query(
        None, description="Filter by read/unread status."
    ),
    category: Optional[NotificationType] = Query(
        None, description="Filter by notification category."
    ),
    sort: Literal["created_at", "read", "category", "title"] = Query(
        "created_at", description="Sort field."
    ),
    order: Literal["asc", "desc"] = Query("desc", description="Sort order."),
    limit: int = Query(20, ge=1, le=100, description="Page size."),
    offset: int = Query(0, ge=0, description="Page offset."),
    current_user: dict = Depends(get_current_user),
    service: NotificationService = Depends(get_notification_service),
) -> dict:
    """Return the caller's notifications with pagination and optional
    read/category filters."""
    owner_id = _owner(current_user)
    rows, total = service.list_notifications(
        owner_id=owner_id,
        read=read,
        category=category.value if category else None,
        sort=sort,
        order=order,
        limit=limit,
        offset=offset,
    )
    return {
        "owner_id": owner_id,
        "notifications": rows,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get(
    "/notifications/unread",
    response_model=dict,
    responses=_ERRORS,
    summary="Count unread notifications",
)
async def count_unread(
    current_user: dict = Depends(get_current_user),
    service: NotificationService = Depends(get_notification_service),
) -> dict:
    """Return the number of unread notifications for the caller."""
    owner_id = _owner(current_user)
    count = service.count_unread(owner_id=owner_id)
    return {"owner_id": owner_id, "unread_count": count}


@router.patch(
    "/notifications/{notification_id}/read",
    response_model=dict,
    responses=_ERRORS,
    summary="Mark one notification as read",
)
async def mark_read(
    notification_id: str = NotificationIdPath,
    current_user: dict = Depends(get_current_user),
    service: NotificationService = Depends(get_notification_service),
) -> dict:
    """Mark one owned notification as read."""
    owner_id = _owner(current_user)
    row = service.mark_read(
        notification_id=notification_id, owner_id=owner_id
    )
    return {"notification": row}


@router.post(
    "/notifications/read-all",
    response_model=dict,
    responses=_ERRORS,
    summary="Mark all notifications as read",
)
async def mark_all_read(
    current_user: dict = Depends(get_current_user),
    service: NotificationService = Depends(get_notification_service),
) -> dict:
    """Mark all of the caller's unread notifications as read."""
    owner_id = _owner(current_user)
    count = service.mark_all_read(owner_id=owner_id)
    return {"owner_id": owner_id, "marked_read": count}


@router.delete(
    "/notifications/{notification_id}",
    response_model=dict,
    responses=_ERRORS,
    summary="Delete one notification",
)
async def delete_notification(
    notification_id: str = NotificationIdPath,
    current_user: dict = Depends(get_current_user),
    service: NotificationService = Depends(get_notification_service),
) -> dict:
    """Delete one owned notification."""
    owner_id = _owner(current_user)
    service.delete_notification(
        notification_id=notification_id, owner_id=owner_id
    )
    return {"status": "deleted", "notification_id": notification_id}


__all__ = ["router"]
