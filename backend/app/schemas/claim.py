"""Claim, evidence and contradiction schemas for the analysis layer."""

from __future__ import annotations

from typing import List, Optional
from uuid import UUID, uuid4

from pydantic import AnyHttpUrl, Field, model_validator

from app.schemas.base import LongText, MediumText, OutputSchema, UtcDatetime, utc_now
from app.schemas.enums import (
    ClaimStatus,
    ClaimType,
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


class Claim(OutputSchema):
    """An extracted factual assertion with its supporting/contradicting evidence."""

    claim_id: UUID = Field(default_factory=uuid4)
    session_id: Optional[UUID] = None
    text: MediumText
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


class Contradiction(OutputSchema):
    """A detected conflict between two claims."""

    contradiction_id: UUID = Field(default_factory=uuid4)
    session_id: Optional[UUID] = None
    claim_a_id: UUID
    claim_b_id: UUID
    severity: ContradictionSeverity = ContradictionSeverity.MEDIUM
    description: LongText
    status: ContradictionStatus = ContradictionStatus.UNRESOLVED
    resolution_note: Optional[MediumText] = None
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
