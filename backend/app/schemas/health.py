"""Health status schema for ``GET /health``."""

from __future__ import annotations

from typing import List, Optional

from pydantic import Field

from app.schemas.base import OutputSchema, ShortText, UtcDatetime, utc_now
from app.schemas.enums import HealthState


class ComponentCheck(OutputSchema):
    """Result of one dependency check (database, vector store, provider, ...)."""

    name: str = Field(..., min_length=1, max_length=64)
    state: HealthState = HealthState.OK
    detail: Optional[ShortText] = None
    duration_ms: Optional[float] = Field(None, ge=0, le=120_000)


class HealthStatus(OutputSchema):
    """Service health payload.

    ``status``/``version``/``environment`` are the original fields; uptime,
    timestamp and checks were added additively.
    """

    status: HealthState = HealthState.OK
    version: str = Field(..., min_length=1, max_length=32)
    environment: Optional[str] = Field(None, min_length=1, max_length=32)
    uptime_seconds: Optional[float] = Field(None, ge=0)
    timestamp: UtcDatetime = Field(default_factory=utc_now)
    checks: List[ComponentCheck] = Field(default_factory=list, max_length=20)
