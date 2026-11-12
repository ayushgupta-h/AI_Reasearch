"""
app/schemas/base.py
===================
Shared strict-model foundations, constrained text types and timestamp rules.

Design decisions
----------------
* **Strict inputs.** ``DomainSchema`` forbids unknown fields so typos and
  contract drift fail loudly instead of silently disappearing.
* **Tolerant outputs.** ``OutputSchema`` ignores unknown fields so a database
  column added by a later stage cannot break response serialization.
* **Timezone-aware timestamps.** Naive datetimes are rejected; JSON output is
  always normalized to UTC with a ``Z`` suffix for consistent serialization.
* **Constrained text.** Every string field in the schema layer uses a bounded
  alias instead of an unbounded ``str``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)
from pydantic.functional_serializers import PlainSerializer
from pydantic.functional_validators import AfterValidator


# ─── Timestamps ──────────────────────────────────────────────────────────────


def utc_now() -> datetime:
    """Timezone-aware current UTC timestamp."""
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime) -> datetime:
    """Reject naive timestamps and normalize aware ones to UTC."""
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(
            "timestamp must include a timezone offset (for example 2026-01-01T00:00:00Z)"
        )
    return value.astimezone(timezone.utc)


def format_utc(value: datetime) -> str:
    """Stable ISO-8601 UTC serialization with a ``Z`` suffix."""
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


UtcDatetime = Annotated[
    datetime,
    AfterValidator(ensure_utc),
    PlainSerializer(format_utc, return_type=str, when_used="json"),
]


# ─── Text types ──────────────────────────────────────────────────────────────

Name = Annotated[str, StringConstraints(min_length=1, max_length=120, strip_whitespace=True)]
Title = Annotated[str, StringConstraints(min_length=3, max_length=200, strip_whitespace=True)]
ShortText = Annotated[str, StringConstraints(min_length=1, max_length=500, strip_whitespace=True)]
MediumText = Annotated[str, StringConstraints(min_length=1, max_length=2_000, strip_whitespace=True)]
LongText = Annotated[str, StringConstraints(min_length=1, max_length=20_000, strip_whitespace=True)]
MarkdownContent = Annotated[str, StringConstraints(min_length=1, max_length=200_000)]
Tag = Annotated[
    str,
    StringConstraints(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9][A-Za-z0-9 _.-]*$"),
]
Slug = Annotated[
    str,
    StringConstraints(min_length=1, max_length=80, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$"),
]
FileName = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=255,
        strip_whitespace=True,
        pattern=r"^[^\\/:*?\"<>|\x00-\x1f]+$",
    ),
]
MimeType = Annotated[
    str,
    StringConstraints(
        min_length=3,
        max_length=120,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+\-]*/[A-Za-z0-9][A-Za-z0-9!#$&^_.+\-]*$",
    ),
]
LanguageCode = Annotated[
    str, StringConstraints(min_length=2, max_length=5, pattern=r"^[a-z]{2}(?:-[A-Z]{2})?$")
]
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[a-fA-F0-9]{64}$")]
Doi = Annotated[
    str, StringConstraints(min_length=7, max_length=255, pattern=r"^10\.\d{4,9}/\S+$")
]


# ─── Model bases ─────────────────────────────────────────────────────────────


class BaseSchema(BaseModel):
    """Shared behaviour: strict parsing plus consistent serialization."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        populate_by_name=True,
        validate_assignment=True,
    )

    def to_payload(self, *, exclude_none: bool = False) -> dict[str, Any]:
        """Serialize to JSON-compatible primitives (UUID -> str, enum -> value)."""
        return self.model_dump(mode="json", exclude_none=exclude_none)


class DomainSchema(BaseSchema):
    """Strict input/domain model: unknown fields are rejected."""


class OutputSchema(BaseSchema):
    """Response/entity model: unknown fields are ignored for forward compatibility."""

    model_config = ConfigDict(
        extra="ignore",
        from_attributes=True,
        str_strip_whitespace=True,
        populate_by_name=True,
        validate_assignment=True,
    )


class TimestampedSchema(OutputSchema):
    """Entity with a creation timestamp and an optional later update."""

    created_at: UtcDatetime = Field(default_factory=utc_now)
    updated_at: Optional[UtcDatetime] = None

    @model_validator(mode="after")
    def _validate_timestamp_order(self) -> "TimestampedSchema":
        if self.updated_at is not None and self.updated_at < self.created_at:
            raise ValueError("updated_at must be greater than or equal to created_at")
        return self
