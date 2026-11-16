"""Transparent source-credibility evaluation schemas.

Credibility here is a *heuristic assessment of available evidence quality*, not
an objective fact or a peer-review verification. Every score is accompanied by
factor-level explanations, missing-information markers, and a confidence value.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import Field

from app.schemas.base import OutputSchema, ShortText, UtcDatetime, utc_now


class CredibilityFactorStatus(str, Enum):
    """How one piece of metadata affected an evaluation."""

    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"
    MISSING = "missing"


class CredibilityLabel(str, Enum):
    """Human-readable summary of the heuristic score."""

    HIGH = "high"
    MODERATE = "moderate"
    LIMITED = "limited"
    INSUFFICIENT_INFORMATION = "insufficient_information"


class CredibilityFactor(OutputSchema):
    """One explainable signal used in a credibility evaluation."""

    factor: ShortText
    status: CredibilityFactorStatus
    weight: float = Field(ge=0, le=1)
    value: Optional[float] = Field(
        None,
        ge=0,
        le=1,
        description="Configured factor value, when the factor has one.",
    )
    reason: ShortText
    observed: Optional[ShortText] = Field(
        None, description="The metadata value considered, if any."
    )


class SourceCredibilityEvaluation(OutputSchema):
    """A per-source heuristic evaluation with an auditable explanation."""

    source_id: Optional[str] = None
    source_url: Optional[str] = None
    title: Optional[str] = None
    publisher: Optional[str] = None
    source_type: Optional[str] = None

    score: Optional[float] = Field(
        None,
        ge=0,
        le=1,
        description="Weighted heuristic score; null when evidence is insufficient.",
    )
    confidence: float = Field(
        ge=0,
        le=1,
        description="Share of configured factor weight backed by available data.",
    )
    label: CredibilityLabel
    factors: List[CredibilityFactor] = Field(default_factory=list)
    missing_fields: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    method: str = "heuristic_v1"
    disclaimer: str = (
        "Heuristic assessment based only on supplied metadata. It is not an "
        "objective credibility fact, a peer-review confirmation, or a reason "
        "to automatically reject a source."
    )
    evaluated_at: UtcDatetime = Field(default_factory=utc_now)


__all__ = [
    "CredibilityFactor",
    "CredibilityFactorStatus",
    "CredibilityLabel",
    "SourceCredibilityEvaluation",
]
