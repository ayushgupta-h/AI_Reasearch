"""
app/services/claim_verification.py
==================================
Claim-level evidence verification for the Research AI pipeline.

Extracts atomic factual claims from research findings, associates each claim
with supporting/contradicting evidence passages from retrieved sources, and
verifies every claim against its evidence using an LLM. Verification output is
always validated against the actual retrieved evidence so the pipeline never
marks an unsupported claim as ``supported``.

Design principles
-----------------
* **Atomic claims only.** Compound statements are split into independent,
  verifiable claims. Opinions, recommendations, and normative statements are
  not treated as factual claims.
* **Evidence-grounded.** Every verification status references at least one
  retrieved evidence passage. Claims with no evidence receive
  ``insufficient_context``.
* **No hallucinated evidence.** Evidence quotes are taken verbatim from
  retrieved source text; the pipeline never invents passages or citations.
* **Five statuses.** ``supported``, ``partially_supported``,
  ``contradicted``, ``unverified``, ``insufficient_context``.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from app.observability import get_logger
from app.schemas.claim import Claim, Evidence
from app.schemas.enums import ClaimStatus, ClaimType, EvidenceStance

logger = get_logger("app.claims.verification")


# ─── Data structures ──────────────────────────────────────────────────────


@dataclass
class ExtractedClaim:
    """An atomic factual claim extracted from research text."""

    claim_id: str
    text: str
    claim_type: str
    source_spans: List[str] = field(default_factory=list)
    confidence: float = 0.5


@dataclass
class EvidencePassage:
    """A passage of evidence retrieved from a source."""

    evidence_id: str
    claim_id: Optional[str]
    source_id: Optional[str]
    source_url: Optional[str]
    source_title: Optional[str]
    quote: str
    stance: str  # EvidenceStance value
    strength: float
    location: Optional[str] = None


@dataclass
class VerificationResult:
    """Result of verifying one claim against its evidence."""

    claim_id: str
    claim_text: str
    status: str  # ClaimStatus value
    confidence: float
    reasoning: str
    supporting_evidence_ids: List[str] = field(default_factory=list)
    contradicting_evidence_ids: List[str] = field(default_factory=list)
    evidence_quotes: List[str] = field(default_factory=list)


# ─── Claim extraction ─────────────────────────────────────────────────────


class ClaimExtractor:
    """
    Splits research findings into atomic, verifiable factual claims.

    Uses deterministic heuristics (sentence splitting + discourse markers)
    rather than an LLM, so extraction is reproducible and never invents
    claims that are not present in the source text.
    """

    # Discourse markers that often join multiple independent claims in one
    # sentence. Splitting on these keeps each atomic claim separately verifiable.
    _CONJUNCTION_SPLIT = re.compile(
        r"\s+(?:but|however|although|whereas|while|yet|nevertheless|"
        r"nonetheless|on the other hand|conversely|instead)\s+",
        re.IGNORECASE,
    )

    _SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")

    # Sentences that express opinion, recommendation, or normative statements
    # are not factual claims and are excluded.
    _OPINION_MARKERS = re.compile(
        r"\b(?:should|ought to|must|recommend|suggest|advocate|"
        r"urge|propose|believe|think|feel|in my opinion|"
        r"it seems|appears to|likely|probably|perhaps|maybe)\b",
        re.IGNORECASE,
    )

    _FACTUAL_SIGNALS = re.compile(
        r"\b(?:is|are|was|were|has|have|had|shows|show|found|"
        r"reported|demonstrates|indicates|reveals|according to|"
        r"improve|improves|improved|reduce|reduces|reduced|"
        r"increase|increases|increased|decrease|decreases|decreased|"
        r"cause|causes|caused|leads|lead|results|associated|correlates|"
        r"outperform|outperforms|affect|affects|perform|performs|"
        r"study|research|data|percent|million|billion|trillion)\b|%",
        re.IGNORECASE,
    )

    def extract(
        self,
        text: str,
        *,
        session_id: Optional[str] = None,
        source_refs: Optional[Sequence[Mapping[str, Any]]] = None,
    ) -> List[ExtractedClaim]:
        """
        Extract atomic factual claims from *text*.

        Parameters
        ----------
        text:
            Research text (analyst briefing, scraped content, or search
            snippets) to mine for claims.
        session_id:
            Optional research session ID to associate with every claim.
        source_refs:
            Optional list of source dicts (url, title, etc.) referenced by
            the text, used to stamp source information on claims.

        Returns
        -------
        List of :class:`ExtractedClaim` instances, each with a unique
        ``claim_id`` and the original claim text preserved.
        """
        if not text or not text.strip():
            return []

        claims: List[ExtractedClaim] = []
        sentences = self._split_sentences(text)

        for sentence in sentences:
            sentence = sentence.strip()
            if len(sentence) < 10:
                continue

            # Skip opinion / recommendation / normative statements
            if self._is_opinion(sentence):
                continue

            # Only keep sentences that look factual
            if not self._FACTUAL_SIGNALS.search(sentence):
                continue

            # Split compound sentences on contrastive conjunctions
            parts = self._CONJUNCTION_SPLIT.split(sentence)
            for part in parts:
                part = part.strip()
                if len(part) < 10:
                    continue
                if self._is_opinion(part):
                    continue
                if not self._FACTUAL_SIGNALS.search(part):
                    continue

                claims.append(
                    ExtractedClaim(
                        claim_id=str(uuid.uuid4()),
                        text=part,
                        claim_type=self._classify_type(part),
                        source_spans=[part],
                        confidence=0.6,
                    )
                )

        # Limit to top 6 atomic claims to optimize pipeline throughput and stay within API rate limits
        claims = claims[:6]

        logger.info(
            "claims.extracted",
            extra={"claim_count": len(claims), "session_id": session_id},
        )
        return claims

    # ── Helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _split_sentences(text: str) -> List[str]:
        """Split text into sentences, preserving abbreviations and decimals."""
        # Protect common abbreviations and decimals from false sentence splits
        protected = re.sub(r"(\d)\.(\d)", r"\1<DOT>\2", text)
        protected = re.sub(
            r"\b(Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St|vs|etc|e\.g|i\.e)\.",
            r"\1<DOT>",
            protected,
            flags=re.IGNORECASE,
        )
        parts = ClaimExtractor._SENTENCE_SPLIT.split(protected)
        sentences = [p.replace("<DOT>", ".").strip() for p in parts if p.strip()]
        return sentences

    @classmethod
    def _is_opinion(cls, sentence: str) -> bool:
        """True when *sentence* expresses opinion/recommendation rather than fact."""
        return bool(cls._OPINION_MARKERS.search(sentence))

    @classmethod
    def _classify_type(cls, text: str) -> str:
        """Classify a claim into a :class:`ClaimType` value."""
        lower = text.lower()
        if re.search(r"\b\d+(\.\d+)?\s?(percent|%|million|billion|trillion)\b", lower):
            return ClaimType.STATISTICAL.value
        if re.search(r"\b(?:cause|causes|caused|leads to|results in|because of)\b", lower):
            return ClaimType.CAUSAL.value
        if re.search(r"\b(?:compared to|versus|vs|more than|less than|higher than|lower than)\b", lower):
            return ClaimType.COMPARATIVE.value
        if re.search(r"\b(?:will|shall|going to|predict|forecast|expect|future)\b", lower):
            return ClaimType.PREDICTIVE.value
        if re.search(r"\b(?:is|are|was|were)\s+(?:a|an|the)\s+\w+\s+(?:which|that|who)\b", lower):
            return ClaimType.DEFINITIONAL.value
        return ClaimType.FACTUAL.value


# ─── Evidence association ─────────────────────────────────────────────────


class EvidenceAssociator:
    """
    Associates retrieved evidence passages with extracted claims.

    Evidence is matched to claims by lexical overlap (shared significant terms)
    between the claim text and each evidence passage. Only passages that
    actually appear in retrieved source text are used — no evidence is
    invented.
    """

    def associate(
        self,
        claims: Sequence[ExtractedClaim],
        evidence_passages: Sequence[Mapping[str, Any]],
    ) -> List[EvidencePassage]:
        """
        Match evidence passages to claims and return enriched evidence records.

        Parameters
        ----------
        claims:
            Extracted claims to match evidence against.
        evidence_passages:
            Retrieved evidence passages, each expected to contain keys like
            ``text``/``quote``, ``url``, ``title``, ``source_id``, ``source``.

        Returns
        -------
        List of :class:`EvidencePassage` records, one per evidence item that
        matches at least one claim, enriched with the matched claim IDs.
        """
        if not claims or not evidence_passages:
            return []

        claim_terms = [
            (c.claim_id, self._significant_terms(c.text)) for c in claims
        ]

        enriched: List[EvidencePassage] = []
        seen_quotes: set = set()

        for passage in evidence_passages:
            quote = self._get_quote(passage)
            if not quote or quote in seen_quotes:
                continue
            seen_quotes.add(quote)

            passage_terms = self._significant_terms(quote)
            matched: List[tuple[str, float, str]] = []

            for claim_id, terms in claim_terms:
                overlap = len(terms & passage_terms)
                if overlap == 0:
                    continue
                # Strength scales with overlap relative to claim complexity
                strength = min(1.0, overlap / max(len(terms), 1))
                stance = (
                    EvidenceStance.CONTRADICTS.value
                    if self._has_negation_near_overlap(quote, terms)
                    else EvidenceStance.SUPPORTS.value
                )
                matched.append((claim_id, strength, stance))

            if not matched:
                continue

            # One passage may bear on more than one claim. Keep a side-aware
            # row for each claim so no side loses its evidence during merge.
            for claim_id, strength, stance in matched:
                enriched.append(
                    EvidencePassage(
                        evidence_id=str(uuid.uuid4()),
                        claim_id=claim_id,
                        source_id=passage.get("source_id"),
                        source_url=passage.get("url"),
                        source_title=passage.get("title") or passage.get("source_name"),
                        quote=quote,
                        stance=stance,
                        strength=round(strength, 3),
                        location=passage.get("location"),
                    )
                )

        logger.info(
            "evidence.associated",
            extra={
                "evidence_count": len(enriched),
                "claim_count": len(claims),
            },
        )
        return enriched

    # ── Helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _significant_terms(text: str) -> set:
        """Return the set of meaningful tokens in *text*."""
        stop = {
            "the", "and", "for", "with", "from", "that", "this", "into",
            "are", "was", "were", "has", "have", "about", "over", "under",
            "between", "through", "during", "before", "after", "above",
            "below", "to", "of", "in", "on", "at", "by", "as", "it", "its",
            "is", "be", "been", "being", "or", "an", "a", "as", "at",
        }
        return {
            t for t in re.findall(r"[a-z0-9]{3,}", text.lower()) if t not in stop
        }

    @staticmethod
    def _get_quote(passage: Mapping[str, Any]) -> Optional[str]:
        """Extract the evidence quote text from a passage mapping."""
        for key in ("quote", "text", "content", "snippet", "passage"):
            value = passage.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @staticmethod
    def _has_negation_near_overlap(quote: str, terms: set) -> bool:
        """Heuristic: check if negation words appear near shared terms."""
        negation = re.findall(r"\b(not|no|never|none|neither|nor|cannot|can't)\b", quote.lower())
        if not negation:
            return False
        # Simple heuristic: if negation present and terms overlap, lean toward contradiction
        return len(terms) > 0


# ─── Claim verification ───────────────────────────────────────────────────


class ClaimVerifier:
    """
    Verifies extracted claims against their associated evidence.

    Uses an LLM for semantic comparison of claim text against evidence
    passages, then validates the LLM's output against the actual evidence
    to prevent unsupported claims from being marked ``supported``.

    Verification statuses (all :class:`ClaimStatus` values):
    * ``supported``        — evidence directly confirms the claim
    * ``partially_supported`` — evidence confirms some aspects but not all
    * ``contradicted``     — evidence directly contradicts the claim
    * ``unverified``       — evidence exists but is inconclusive
    * ``insufficient_context`` — no relevant evidence available
    """

    # System prompt instructs the LLM to return structured verification output.
    _SYSTEM_PROMPT = (
        "You are a meticulous fact-checker. Verify the claim below against "
        "the provided evidence passages. "
        "Respond ONLY with valid JSON matching this schema:\n"
        "{\n"
        '  "status": "supported | partially_supported | contradicted | unverified | insufficient_context",\n'
        '  "confidence": 0.0 to 1.0,\n'
        '  "reasoning": "brief explanation",\n'
        '  "supporting_evidence_ids": ["...", ...],\n'
        '  "contradicting_evidence_ids": ["...", ...]\n'
        "}\n"
        "Rules:\n"
        "1. Only mark 'supported' when at least one evidence passage directly "
        "confirms the claim.\n"
        "2. Mark 'partially_supported' when evidence confirms some but not all "
        "aspects of the claim.\n"
        "3. Mark 'contradicted' when at least one evidence passage directly "
        "contradicts the claim.\n"
        "4. Mark 'unverified' when evidence exists but is inconclusive or ambiguous.\n"
        "5. Mark 'insufficient_context' when there is no relevant evidence.\n"
        "6. Never invent evidence. Reference only the provided evidence passage IDs.\n"
        "7. Do not treat opinion or recommendation as a verifiable fact.\n"
    )

    def __init__(self, llm: Optional[Any] = None) -> None:
        self._llm = llm

    def verify(
        self,
        claim: ExtractedClaim,
        evidence: Sequence[EvidencePassage],
    ) -> VerificationResult:
        """
        Verify *claim* against its associated *evidence*.

        Returns a :class:`VerificationResult` with one of the five
        :class:`ClaimStatus` values. The LLM's verdict is always cross-checked
        against the actual evidence before being finalized.
        """
        if not evidence:
            return VerificationResult(
                claim_id=claim.claim_id,
                claim_text=claim.text,
                status=ClaimStatus.INSUFFICIENT_CONTEXT.value,
                confidence=0.0,
                reasoning="No evidence passages available for this claim.",
            )

        # Build evidence context for the LLM
        evidence_text = self._format_evidence(evidence)

        # Ask the LLM for a preliminary verification
        llm_result: Optional[Dict[str, Any]] = None
        if self._llm is not None:
            try:
                raw = self._llm.invoke(
                    [
                        {"role": "system", "content": self._SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": (
                                f"CLAIM TO VERIFY:\n{claim.text}\n\n"
                                f"EVIDENCE PASSAGES:\n{evidence_text}"
                            ),
                        },
                    ]
                )
                content = self._extract_llm_content(raw)
                llm_result = self._parse_verification_json(content)
            except Exception as exc:  # never fail the pipeline on verification
                logger.exception("claim.verification_llm_failed")
                llm_result = None

        # Cross-check and finalize against actual evidence
        return self._finalize(claim, evidence, llm_result)

    def verify_all(
        self,
        claims: Sequence[ExtractedClaim],
        evidence_map: Mapping[str, Sequence[EvidencePassage]],
    ) -> List[VerificationResult]:
        """Verify every claim in *claims* using *evidence_map* keyed by claim_id."""
        results: List[VerificationResult] = []
        for claim in list(claims)[:6]:
            evidence = evidence_map.get(claim.claim_id, [])
            results.append(self.verify(claim, evidence))
        return results

    # ── Helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _extract_llm_content(raw: Any) -> str:
        """Extract text content from an LLM response object."""
        if isinstance(raw, str):
            return raw
        content = getattr(raw, "content", None)
        if content is not None:
            return str(content)
        if isinstance(raw, Mapping):
            return str(raw.get("content", raw.get("text", "")))
        return str(raw)

    @staticmethod
    def _parse_verification_json(content: str) -> Optional[Dict[str, Any]]:
        """Parse the LLM's JSON verification output, tolerating markdown fences."""
        text = content.strip()
        # Strip markdown code fences if present
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        try:
            import json
            return json.loads(text)
        except (ValueError, json.JSONDecodeError):
            return None

    @staticmethod
    def _format_evidence(evidence: Sequence[EvidencePassage]) -> str:
        """Format evidence passages for the LLM prompt."""
        lines: List[str] = []
        for i, e in enumerate(evidence, 1):
            lines.append(
                f"[{i}] id={e.evidence_id} source={e.source_url or 'unknown'} "
                f"stance={e.stance} strength={e.strength}\n    \"{e.quote}\""
            )
        return "\n".join(lines)

    @classmethod
    def _finalize(
        cls,
        claim: ExtractedClaim,
        evidence: Sequence[EvidencePassage],
        llm_result: Optional[Dict[str, Any]],
    ) -> VerificationResult:
        """
        Finalize the verification result, cross-checking the LLM verdict
        against actual evidence.
        """
        supporting: List[EvidencePassage] = [
            e for e in evidence if e.stance == EvidenceStance.SUPPORTS.value
        ]
        contradicting: List[EvidencePassage] = [
            e for e in evidence if e.stance == EvidenceStance.CONTRADICTS.value
        ]

        # Determine the evidence-based floor/ceiling for the status
        has_support = len(supporting) > 0
        has_contradiction = len(contradicting) > 0

        llm_status = (llm_result or {}).get("status", "") if llm_result else ""
        llm_confidence = float((llm_result or {}).get("confidence", 0.0) or 0.0)
        llm_reasoning = str((llm_result or {}).get("reasoning", "") or "")

        if not has_support and not has_contradiction:
            # Neutral evidence only — cannot support or contradict
            status = ClaimStatus.UNVERIFIED.value
            confidence = min(llm_confidence, 0.5)
            reasoning = (
                llm_reasoning or "Evidence exists but is neutral or ambiguous."
            )
        elif has_contradiction and not has_support:
            status = ClaimStatus.CONTRADICTED.value
            confidence = max(llm_confidence, 0.6)
            reasoning = (
                llm_reasoning
                or f"Contradicting evidence found ({len(contradicting)} passage(s))."
            )
        elif has_support and has_contradiction:
            # Both support and contradiction — partial at best
            status = ClaimStatus.PARTIALLY_SUPPORTED.value
            confidence = max(llm_confidence, 0.5)
            reasoning = (
                llm_reasoning
                or f"Conflicting evidence: {len(supporting)} supporting, "
                f"{len(contradicting)} contradicting passage(s)."
            )
        else:
            # Support only — the LLM verdict is authoritative here
            if llm_status == ClaimStatus.SUPPORTED.value:
                status = ClaimStatus.SUPPORTED.value
                confidence = max(llm_confidence, 0.7)
                reasoning = llm_reasoning or "Evidence directly supports the claim."
            elif llm_status == ClaimStatus.PARTIALLY_SUPPORTED.value:
                status = ClaimStatus.PARTIALLY_SUPPORTED.value
                confidence = max(llm_confidence, 0.5)
                reasoning = (
                    llm_reasoning or "Evidence partially supports the claim."
                )
            else:
                # LLM was uncertain despite supporting evidence → unverified
                status = ClaimStatus.UNVERIFIED.value
                confidence = max(llm_confidence, 0.4)
                reasoning = (
                    llm_reasoning or "Supporting evidence exists but is inconclusive."
                )

        # Override: if LLM said 'supported' but no supporting evidence exists,
        # downgrade to unverified — never invent support
        if status == ClaimStatus.SUPPORTED.value and not has_support:
            status = ClaimStatus.UNVERIFIED.value
            confidence = min(confidence, 0.4)
            reasoning = (
                "LLM marked as supported but no supporting evidence was found "
                "in the retrieved passages. Downgraded to unverified."
            )

        return VerificationResult(
            claim_id=claim.claim_id,
            claim_text=claim.text,
            status=status,
            confidence=round(confidence, 3),
            reasoning=reasoning,
            supporting_evidence_ids=[e.evidence_id for e in supporting],
            contradicting_evidence_ids=[e.evidence_id for e in contradicting],
            evidence_quotes=[e.quote for e in evidence],
        )


__all__ = [
    "ClaimExtractor",
    "EvidenceAssociator",
    "ClaimVerifier",
    "ExtractedClaim",
    "EvidencePassage",
    "VerificationResult",
]
