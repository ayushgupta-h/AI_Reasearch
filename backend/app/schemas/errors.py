"""
app/schemas/errors.py
=====================
Error contract.

``ApiError`` describes the payload the existing exception handlers already emit
(``detail``, ``error_code``, ``request_id``, optional ``details``). It is used
for OpenAPI documentation only — runtime error responses are unchanged.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import Field

from app.schemas.base import OutputSchema, UtcDatetime
from app.schemas.enums import ErrorCode


class ApiError(OutputSchema):
    """Standard error body returned by the API."""

    detail: str = Field(..., min_length=1, max_length=2_000)
    error_code: ErrorCode
    request_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="Correlation ID of the failed request.",
    )
    details: Optional[Dict[str, Any]] = None
    timestamp: Optional[UtcDatetime] = Field(
        None, description="Optional server timestamp for the failure."
    )
