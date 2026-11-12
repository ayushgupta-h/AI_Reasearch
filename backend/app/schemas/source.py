"""Research source schema: one retrieved document/web page/paper."""

from __future__ import annotations

from typing import Dict, Optional
from uuid import UUID, uuid4

from pydantic import AnyHttpUrl, Field, StringConstraints, model_validator
from typing import Annotated

from app.schemas.base import Doi, LanguageCode, MediumText, OutputSchema, ShortText, Title, UtcDatetime, utc_now
from app.schemas.enums import SourceType

MetadataKey = Annotated[str, StringConstraints(min_length=1, max_length=40)]
MetadataValue = Annotated[str, StringConstraints(max_length=200)]


class ResearchSource(OutputSchema):
    """A source retrieved by the search/reader agents."""

    source_id: UUID = Field(default_factory=uuid4)
    session_id: Optional[UUID] = None
    title: Title
    url: Optional[AnyHttpUrl] = None
    doi: Optional[Doi] = None
    source_name: Optional[ShortText] = Field(
        None, description="Adapter that produced the result, e.g. 'arXiv'."
    )
    source_type: SourceType = SourceType.WEB
    snippet: Optional[MediumText] = None
    published_at: Optional[UtcDatetime] = None
    retrieved_at: UtcDatetime = Field(default_factory=utc_now)
    credibility_score: Optional[float] = Field(None, ge=0, le=1)
    language: LanguageCode = "en"
    metadata: Dict[MetadataKey, MetadataValue] = Field(default_factory=dict, max_length=20)

    @model_validator(mode="after")
    def _validate_identifiers(self) -> "ResearchSource":
        if self.url is None and self.doi is None:
            raise ValueError("a source must include at least one of url or doi")
        if self.published_at is not None and self.published_at > self.retrieved_at:
            raise ValueError("published_at cannot be later than retrieved_at")
        return self


__all__ = ["ResearchSource", "SourceType"]
