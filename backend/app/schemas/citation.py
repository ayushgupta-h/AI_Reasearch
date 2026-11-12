"""Citation schema: a report reference and its live verification result."""

from __future__ import annotations

from typing import Optional
from uuid import UUID, uuid4

from pydantic import AnyHttpUrl, Field, model_validator

from app.schemas.base import Doi, MediumText, OutputSchema, UtcDatetime, utc_now
from app.schemas.enums import CitationFormat, CitationStatus


class Citation(OutputSchema):
    """A citation extracted from a report, optionally verified over HTTP."""

    citation_id: UUID = Field(default_factory=uuid4)
    session_id: Optional[UUID] = None
    source_id: Optional[UUID] = None
    raw_text: MediumText
    url: Optional[AnyHttpUrl] = None
    doi: Optional[Doi] = None
    style: CitationFormat = CitationFormat.APA
    status: CitationStatus = CitationStatus.UNVERIFIED
    verified_at: Optional[UtcDatetime] = None
    http_status: Optional[int] = Field(None, ge=100, le=599)
    response_ms: Optional[float] = Field(None, ge=0, le=120_000)

    @model_validator(mode="after")
    def _validate_citation(self) -> "Citation":
        if self.url is None and self.doi is None:
            raise ValueError("a citation must include at least one of url or doi")
        if self.status == CitationStatus.VERIFIED:
            if self.verified_at is None:
                raise ValueError("verified citations must include verified_at")
            if self.http_status is None or not 200 <= self.http_status < 400:
                raise ValueError(
                    "verified citations require a 2xx/3xx http_status"
                )
        if self.status == CitationStatus.UNVERIFIED and self.verified_at is not None:
            raise ValueError("unverified citations cannot include verified_at")
        if self.verified_at is not None and self.verified_at > utc_now():
            raise ValueError("verified_at cannot be in the future")
        return self
