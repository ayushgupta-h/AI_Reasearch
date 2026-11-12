"""
app/schemas/common.py
=====================
Cross-cutting contract primitives: pagination and the standardized envelope.

The envelope is opt-in (``?envelope=true``) on legacy read endpoints. It is
never applied to SSE streams or binary downloads, which would break clients.
"""

from __future__ import annotations

from typing import Any, Generic, List, Optional, TypeVar

from pydantic import Field, model_validator

from app.schemas.base import BaseSchema, DomainSchema, OutputSchema

T = TypeVar("T")

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 20


class PaginationParams(DomainSchema):
    """Validated pagination query parameters."""

    limit: int = Field(
        DEFAULT_PAGE_SIZE,
        ge=1,
        le=MAX_PAGE_SIZE,
        description="Maximum number of items to return.",
    )
    offset: int = Field(0, ge=0, le=1_000_000, description="Number of items to skip.")


class PaginationMeta(OutputSchema):
    """Pagination information attached to a page of results."""

    limit: int = Field(..., ge=1, le=MAX_PAGE_SIZE)
    offset: int = Field(..., ge=0)
    total: int = Field(..., ge=0)
    returned: int = Field(..., ge=0)
    has_more: bool

    @model_validator(mode="after")
    def _validate_consistency(self) -> "PaginationMeta":
        if self.offset + self.returned > self.total:
            raise ValueError("offset + returned cannot exceed total")
        expected_has_more = self.offset + self.returned < self.total
        if self.has_more != expected_has_more:
            raise ValueError("has_more must be true exactly when more items remain")
        return self


class Page(OutputSchema, Generic[T]):
    """A page of items plus its pagination metadata."""

    items: List[T]
    pagination: PaginationMeta


class ApiResponse(BaseSchema, Generic[T]):
    """Standardized success envelope.

    Shape::

        {"success": true, "data": {...}, "message": "OK", "request_id": "..."}
    """

    success: bool = True
    data: T
    message: str = Field(default="OK", min_length=1, max_length=500)
    request_id: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=64,
        description="Correlation ID of the originating request.",
    )

    @model_validator(mode="after")
    def _validate_success_flag(self) -> "ApiResponse[T]":
        if not self.success:
            raise ValueError("use ApiError for failures; ApiResponse is a success envelope")
        return self


def envelope_payload(
    data: Any, *, message: str = "OK", request_id: Optional[str] = None
) -> dict[str, Any]:
    """Build an envelope dict from an already-serialized payload.

    Used where the payload type is a runtime union and constructing a generic
    model would be redundant.
    """
    body: dict[str, Any] = {"success": True, "data": data, "message": message}
    if request_id:
        body["request_id"] = request_id
    return body
