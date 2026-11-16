"""Claim, evidence and contradiction schemas for the analysis layer."""

from __future__ import annotations

from typing import List, Optional
from uuid import UUID, uuid4

from pydantic import AnyHttpUrl, Field, model_validator

from app.schemas.base import LongText, MediumText, OutputSchema, ShortText, UtcDatetime, utc_now
from app.schemas.enums import (
    ClaimStatus,
    ClaimType,
    ContradictionCategory,
    ContradictionSeverity,
    ContradictionStatus,
    EvidenceStance,
)


class Evidence(OutputSchema):
    """A quotation/observation that bears on a claim."""

    evidence_id: UUID = Field(default_factory=uuid4)
    claim_id: Optional[UUID] = None
    source_id: Optional[UUID] = None
    quote: MediumText
    url: Optional[AnyHttpUrl] = None
    stance: EvidenceStance = EvidenceStance.SUPPORTS
    strength: float = Field(0.5, ge=0, le=1)
    retrieved_at: UtcDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _validate_evidence_anchor(self) -> "Evidence":
        if self.source_id is None and self.url is None:
            raise ValueError("evidence must reference a source_id or a url")
        return self


class ContradictionAnalysis(OutputSchema):
    """Flexible analysis result keyed by claim identifiers from a workflow.

    Unlike :class:`Contradiction`, this schema accepts string IDs so it can be
    used before claims have been persisted as UUID-backed database rows.
    """

    analysis_id: UUID = Field(default_factory=uuid4)
    claim_a_id: str
    claim_b_id: str
    claim_a_text: Optional[MediumText] = None
    claim_b_text: Optional[MediumText] = None
    category: ContradictionCategory
    severity: ContradictionSeverity = ContradictionSeverity.MEDIUM
    status: ContradictionStatus = ContradictionStatus.UNRESOLVED
    description: LongText
    differences: List[ContradictionDifference] = Field(default_factory=list, max_length=20)
    claim_a_evidence_ids: List[str] = Field(default_factory=list, max_length=50)
    claim_b_evidence_ids: List[str] = Field(default_factory=list, max_length=50)
    confidence: float = Field(0.5, ge=0, le=1)
    requires_human_review: bool = True
    detected_at: UtcDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _validate_distinct_claims(self) -> "ContradictionAnalysis":
        if self.claim_a_id == self.claim_b_id:
            raise ValueError("a contradiction analysis requires two different claims")
        return self


__all__ = [
    "Claim",
    "Contradiction",
    "ContradictionAnalysis",
    "ContradictionDifference",
    "Evidence",
]


class Claim(OutputSchema):
    """An extracted factual assertion with its supporting/contradicting evidence."""

    claim_id: UUID = Field(default_factory=uuid4)
    session_id: Optional[UUID] = None
    text: MediumText
    # Optional context supplied by the claim-verification layer. These fields
    # make contradiction analysis explainable without guessing methodology or
    # population from prose.
    subject: Optional[ShortText] = None
    research_question: Optional[MediumText] = None
    conclusion: Optional[MediumText] = None
    methodology: Optional[MediumText] = None
    population: Optional[ShortText] = None
    time_period: Optional[ShortText] = None
    evidence_ids: List[UUID] = Field(default_factory=list, max_length=50)
    claim_type: ClaimType = ClaimType.FACTUAL
    status: ClaimStatus = ClaimStatus.UNVERIFIED
    confidence: float = Field(0.5, ge=0, le=1)
    supporting_evidence: List[Evidence] = Field(default_factory=list, max_length=20)
    contradicting_evidence: List[Evidence] = Field(default_factory=list, max_length=20)
    citation_ids: List[UUID] = Field(default_factory=list, max_length=20)
    created_at: UtcDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _validate_evidence_alignment(self) -> "Claim":
        for item in self.supporting_evidence:
            if item.stance not in (EvidenceStance.SUPPORTS, EvidenceStance.CONTEXTUAL):
                raise ValueError(
                    "supporting_evidence items must have stance 'supports' or 'contextual'"
                )
        for item in self.contradicting_evidence:
            if item.stance != EvidenceStance.CONTRADICTS:
                raise ValueError(
                    "contradicting_evidence items must have stance 'contradicts'"
                )
        if self.status == ClaimStatus.SUPPORTED and not self.supporting_evidence:
            raise ValueError("supported claims require supporting evidence")
        if self.status == ClaimStatus.CONTRADICTED and not self.contradicting_evidence:
            raise ValueError("contradicted claims require contradicting evidence")
        return self


class ContradictionDifference(OutputSchema):
    """One contextual, methodological, or temporal difference."""

    field: ShortText
    claim_a_value: Optional[ShortText] = None
    claim_b_value: Optional[ShortText] = None
    explanation: ShortText


class Contradiction(OutputSchema):
    """A detected conflict between two claims."""

    contradiction_id: UUID = Field(default_factory=uuid4)
    session_id: Optional[UUID] = None
    claim_a_id: UUID
    claim_b_id: UUID
    category: ContradictionCategory = ContradictionCategory.INSUFFICIENT_INFORMATION
    severity: ContradictionSeverity = ContradictionSeverity.MEDIUM
    description: LongText
    status: ContradictionStatus = ContradictionStatus.UNRESOLVED
    resolution_note: Optional[MediumText] = None
    claim_a_evidence_ids: List[UUID] = Field(default_factory=list, max_length=50)
    claim_b_evidence_ids: List[UUID] = Field(default_factory=list, max_length=50)
    differences: List[ContradictionDifference] = Field(default_factory=list, max_length=20)
    confidence: float = Field(0.5, ge=0, le=1)
    requires_human_review: bool = True
    detected_at: UtcDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _validate_contradiction(self) -> "Contradiction":
        if self.claim_a_id == self.claim_b_id:
            raise ValueError("a contradiction must reference two different claims")
        if (
            self.status != ContradictionStatus.UNRESOLVED
            and not self.resolution_note
        ):
            raise ValueError("resolved/ambiguous contradictions require a resolution_note")
        return self
