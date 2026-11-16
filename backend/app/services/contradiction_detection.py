"""Deterministic, explainable claim-contradiction detection.

The service compares only related claims and classifies *why* they differ. It
never chooses a winner, treats newer claims as more correct, or asks an LLM to
resolve a scientific disagreement. Every result keeps both claims and both
evidence sets available for human review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import combinations
from typing import Any, Iterable, Mapping, Optional, Sequence

from app.observability import get_logger
from app.schemas.claim import (
    ContradictionAnalysis,
    ContradictionDifference,
)
from app.schemas.enums import (
    ContradictionCategory,
    ContradictionSeverity,
    ContradictionStatus,
)

logger = get_logger("app.contradiction_detection")

_STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "into", "are",
    "was", "were", "has", "have", "had", "but", "not", "can", "could",
    "would", "should", "their", "there", "they", "than", "then", "also",
    "about", "over", "under", "such", "which", "while", "where", "when",
    "what", "who", "all", "any", "may", "might", "must", "only", "more",
    "most", "some", "our", "its", "it", "as", "at", "by", "be", "been",
    "is", "of", "on", "or", "to", "in", "a", "an",
}

_POSITIVE_TERMS = (
    "effective",     "improve", "improves", "improved", "benefit", "beneficial", "safe",
    "support", "supports", "superior", "advantage", "success", "successful",
    "accurate", "accuracy", "positive", "protective", "efficacious",
)
_NEGATIVE_TERMS = (
    "ineffective", "harmful", "unsafe", "worse", "worsen", "worsens", "worsened",
    "fail", "fails", "failed", "inferior", "disadvantage", "negative",
    "no effect", "no benefit", "not effective", "not beneficial",
)
_UP_TERMS = (
    "increase", "increases", "increased", "increasing", "higher", "more", "rise",
    "rises", "rose", "grow", "grew", "improve", "improved", "gain",
)
_DOWN_TERMS = (
    "decrease", "decreases", "decreased", "decreasing", "lower", "less", "reduce",
    "reduces", "reducing", "reduced", "reduction", "decline", "declined", "fall", "fell", "worse",
)
_NEGATION_PATTERN = re.compile(
    r"\b(?:not|no|never|without|neither|none|fails?|failed|unable|"
    r"cannot|can't|does not|do not|did not|isn't|aren't|wasn't|weren't)\b",
    re.IGNORECASE,
)


@dataclass
class _ClaimView:
    claim_id: str
    text: str
    subject: str
    research_question: str
    conclusion: str
    methodology: str
    population: str
    time_period: str
    evidence_ids: tuple[str, ...]
    tokens: set[str]
    subject_tokens: set[str]
    polarity: int
    direction: int


class ContradictionDetectionService:
    """Compare claims with conservative, inspectable heuristics."""

    def __init__(
        self,
        *,
        relatedness_threshold: float = 0.20,
        direct_similarity_threshold: float = 0.55,
    ) -> None:
        self.relatedness_threshold = relatedness_threshold
        self.direct_similarity_threshold = direct_similarity_threshold

    # ── Public API ───────────────────────────────────────────────────────

    def detect(
        self,
        claims: Iterable[Mapping[str, Any] | Any],
        *,
        include_non_contradictions: bool = False,
    ) -> list[ContradictionAnalysis]:
        """Analyze every related claim pair.

        Unrelated pairs are skipped. By default ``no_contradiction`` pairs are
        omitted because they are not potential contradiction records; callers
        that need every classification can set the flag.
        """
        views = [
            self._view(claim, index=index)
            for index, claim in enumerate(claims)
        ]
        results: list[ContradictionAnalysis] = []
        for left, right in combinations(views, 2):
            try:
                analysis = self._analyze(left, right)
            except Exception:
                logger.exception("contradiction_detection.pair_failed")
                continue
            if analysis is None:
                continue
            if (
                not include_non_contradictions
                and analysis.category is ContradictionCategory.NO_CONTRADICTION
            ):
                continue
            results.append(analysis)
        return results

    def analyze_pair(
        self,
        claim_a: Mapping[str, Any] | Any,
        claim_b: Mapping[str, Any] | Any,
        *,
        index_a: int = 0,
        index_b: int = 1,
    ) -> Optional[ContradictionAnalysis]:
        """Classify one pair; return ``None`` when the claims are unrelated."""
        return self._analyze(
            self._view(claim_a, index=index_a),
            self._view(claim_b, index=index_b),
        )

    # ── Claim normalisation ───────────────────────────────────────────────

    def _view(self, claim: Mapping[str, Any] | Any, *, index: int) -> _ClaimView:
        data = self._as_mapping(claim)
        nested = data.get("metadata")
        if isinstance(nested, Mapping):
            for key in ("subject", "research_question", "methodology",
                        "population", "time_period", "conclusion"):
                if data.get(key) in (None, "", [], {}) and nested.get(key) not in (
                    None, "", [], {}
                ):
                    data[key] = nested[key]

        text = self._string(data.get("text") or data.get("claim")) or ""
        subject = self._string(data.get("subject")) or ""
        research_question = self._string(data.get("research_question")) or subject
        conclusion = self._string(data.get("conclusion")) or text
        methodology = self._string(data.get("methodology")) or ""
        population = self._string(data.get("population")) or ""
        time_period = self._string(data.get("time_period")) or ""
        claim_id = self._string(data.get("claim_id") or data.get("id"))
        if not claim_id:
            claim_id = f"claim-{index + 1}"

        evidence_ids: list[str] = []
        explicit = data.get("evidence_ids")
        if isinstance(explicit, (list, tuple)):
            evidence_ids.extend(
                value for value in (self._string(item) for item in explicit) if value
            )
        for key in ("supporting_evidence", "contradicting_evidence"):
            for evidence in data.get(key, []) or []:
                ev = self._as_mapping(evidence)
                evidence_id = self._string(
                    ev.get("evidence_id") or ev.get("id")
                )
                if evidence_id:
                    evidence_ids.append(evidence_id)
        evidence_ids = list(dict.fromkeys(evidence_ids))

        tokens = self._terms(text or conclusion)
        subject_tokens = self._terms(subject or research_question or text)
        return _ClaimView(
            claim_id=claim_id,
            text=text or conclusion,
            subject=subject or research_question,
            research_question=research_question,
            conclusion=conclusion,
            methodology=methodology,
            population=population,
            time_period=time_period,
            evidence_ids=tuple(evidence_ids),
            tokens=tokens,
            subject_tokens=subject_tokens or tokens,
            polarity=self._polarity(conclusion or text),
            direction=self._direction(conclusion or text),
        )

    # ── Pair analysis ────────────────────────────────────────────────────

    def _analyze(
        self, left: _ClaimView, right: _ClaimView
    ) -> Optional[ContradictionAnalysis]:
        if not self._related(left, right):
            return None

        differences: list[ContradictionDifference] = []
        for field, a_value, b_value in (
            ("methodology", left.methodology, right.methodology),
            ("population", left.population, right.population),
            ("time_period", left.time_period, right.time_period),
        ):
            difference = self._difference(field, a_value, b_value)
            if difference is not None:
                differences.append(difference)

        conflict = self._conflict(left, right)
        similarity = self._similarity(left.tokens, right.tokens)
        method_diff = any(d.field == "methodology" for d in differences)
        population_diff = any(d.field == "population" for d in differences)
        time_diff = any(d.field == "time_period" for d in differences)

        if conflict:
            if method_diff:
                category = ContradictionCategory.METHODOLOGICAL_DIFFERENCE
                reason = (
                    "The claims reach incompatible conclusions, but their "
                    "methodologies differ. This may explain the disagreement; "
                    "neither claim is treated as correct."
                )
                severity = ContradictionSeverity.LOW
                confidence = 0.62
            elif population_diff:
                category = ContradictionCategory.CONTEXTUAL_DIFFERENCE
                reason = (
                    "The claims reach incompatible conclusions for different "
                    "populations or contexts. This is a contextual difference, "
                    "not a resolved scientific contradiction."
                )
                severity = ContradictionSeverity.LOW
                confidence = 0.62
            elif time_diff:
                category = ContradictionCategory.TEMPORAL_DIFFERENCE
                reason = (
                    "The claims reach incompatible conclusions for different "
                    "time periods. The difference may reflect change over time; "
                    "neither claim is automatically preferred."
                )
                severity = ContradictionSeverity.LOW
                confidence = 0.62
            elif similarity >= self.direct_similarity_threshold:
                category = ContradictionCategory.DIRECT_CONTRADICTION
                reason = (
                    "The claims address the same subject and assert incompatible "
                    "conclusions. Both claims and their evidence are preserved "
                    "for human review."
                )
                severity = ContradictionSeverity.HIGH
                confidence = 0.90
            else:
                category = ContradictionCategory.PARTIAL_CONTRADICTION
                reason = (
                    "The claims overlap but only partially assert incompatible "
                    "conclusions. The scope of the disagreement remains unresolved."
                )
                severity = ContradictionSeverity.MEDIUM
                confidence = 0.72
        elif method_diff:
            category = ContradictionCategory.METHODOLOGICAL_DIFFERENCE
            reason = (
                "The claims do not assert an explicit contradiction, but their "
                "methodologies differ. The methodological difference is recorded "
                "for interpretation."
            )
            severity = ContradictionSeverity.LOW
            confidence = 0.55
        elif population_diff:
            category = ContradictionCategory.CONTEXTUAL_DIFFERENCE
            reason = (
                "The claims do not assert an explicit contradiction, but they "
                "refer to different populations or contexts."
            )
            severity = ContradictionSeverity.LOW
            confidence = 0.55
        elif time_diff:
            category = ContradictionCategory.TEMPORAL_DIFFERENCE
            reason = (
                "The claims do not assert an explicit contradiction, but they "
                "refer to different time periods."
            )
            severity = ContradictionSeverity.LOW
            confidence = 0.55
        elif self._compatible_stance(left, right):
            category = ContradictionCategory.NO_CONTRADICTION
            reason = (
                "The claims address the same subject but their detected stances "
                "are compatible. No contradiction is asserted."
            )
            severity = ContradictionSeverity.LOW
            confidence = 0.80
        else:
            category = ContradictionCategory.INSUFFICIENT_INFORMATION
            reason = (
                "The claims are related, but there is not enough explicit "
                "conclusion or context metadata to determine whether they "
                "conflict. The pair remains unresolved."
            )
            severity = ContradictionSeverity.LOW
            confidence = 0.25

        description = (
            f"Claim A [{left.claim_id}]: {left.text or left.conclusion}\n"
            f"Claim B [{right.claim_id}]: {right.text or right.conclusion}\n"
            f"Assessment: {reason}"
        )
        return ContradictionAnalysis(
            claim_a_id=left.claim_id,
            claim_b_id=right.claim_id,
            claim_a_text=left.text or left.conclusion,
            claim_b_text=right.text or right.conclusion,
            category=category,
            severity=severity,
            status=ContradictionStatus.UNRESOLVED,
            description=description,
            differences=differences,
            claim_a_evidence_ids=list(left.evidence_ids),
            claim_b_evidence_ids=list(right.evidence_ids),
            confidence=confidence,
            requires_human_review=category is not ContradictionCategory.NO_CONTRADICTION,
        )

    # ── Signals ──────────────────────────────────────────────────────────

    def _related(self, left: _ClaimView, right: _ClaimView) -> bool:
        subject_similarity = self._similarity(
            left.subject_tokens, right.subject_tokens
        )
        text_similarity = self._similarity(left.tokens, right.tokens)
        shared_subject = bool(left.subject_tokens & right.subject_tokens)
        shared_text = bool(left.tokens & right.tokens)
        if not shared_subject and not shared_text:
            return False
        return max(subject_similarity, text_similarity) >= self.relatedness_threshold

    def _conflict(self, left: _ClaimView, right: _ClaimView) -> bool:
        if left.polarity and right.polarity and left.polarity != right.polarity:
            return True
        if left.direction and right.direction and left.direction != right.direction:
            return True
        return False

    @staticmethod
    def _compatible_stance(left: _ClaimView, right: _ClaimView) -> bool:
        # Treat evaluative polarity and directional wording as usable stance
        # signals only when both claims expose the same kind of signal. This
        # avoids declaring two directional claims compatible merely because
        # neither contains a sentiment word.
        if left.polarity and right.polarity:
            return left.polarity == right.polarity
        if left.direction and right.direction:
            return left.direction == right.direction
        return False

    def _difference(
        self, field: str, a_value: str, b_value: str
    ) -> Optional[ContradictionDifference]:
        if not a_value or not b_value:
            return None
        a_norm = self._normalise(a_value)
        b_norm = self._normalise(b_value)
        if a_norm == b_norm:
            return None
        if field == "time_period":
            if set(re.findall(r"\d{4}", a_norm)) == set(re.findall(r"\d{4}", b_norm)):
                return None
        elif self._similarity(self._terms(a_value), self._terms(b_value)) >= 0.50:
            return None

        labels = {
            "methodology": "different research methods",
            "population": "different populations or contexts",
            "time_period": "different time periods",
        }
        return ContradictionDifference(
            field=field,
            claim_a_value=a_value[:480],
            claim_b_value=b_value[:480],
            explanation=(
                f"The claims use {labels.get(field, 'different contexts')}. "
                "This difference is recorded and is not resolved automatically."
            ),
        )

    @staticmethod
    def _polarity(text: str) -> int:
        lower = text.lower()
        if "not only" in lower:
            lower = lower.replace("not only", "")
        positive = any(re.search(rf"\b{re.escape(term)}\b", lower) for term in _POSITIVE_TERMS)
        negative = any(re.search(rf"\b{re.escape(term)}\b", lower) for term in _NEGATIVE_TERMS)
        if any(
            phrase in lower
            for phrase in (
                "no effect", "no significant", "not effective",
                "not beneficial", "cannot improve", "fails to improve",
            )
        ):
            return -1
        base = 1 if positive else -1 if negative else 0
        if _NEGATION_PATTERN.search(lower):
            if base:
                return -base
            return -1
        return base

    @staticmethod
    def _direction(text: str) -> int:
        lower = text.lower()
        up = any(re.search(rf"\b{re.escape(term)}\b", lower) for term in _UP_TERMS)
        down = any(re.search(rf"\b{re.escape(term)}\b", lower) for term in _DOWN_TERMS)
        if up == down:
            return 0
        return 1 if up else -1

    @staticmethod
    def _terms(text: str) -> set[str]:
        return {
            token
            for token in re.findall(r"[a-z0-9]{3,}", text.lower())
            if token not in _STOPWORDS
        }

    @staticmethod
    def _similarity(left: set[str], right: set[str]) -> float:
        if not left or not right:
            return 0.0
        return len(left & right) / max(1, min(len(left), len(right)))

    @staticmethod
    def _normalise(value: str) -> str:
        return " ".join(re.findall(r"[a-z0-9]+", value.lower()))

    @staticmethod
    def _as_mapping(value: Mapping[str, Any] | Any) -> dict[str, Any]:
        if isinstance(value, Mapping):
            return dict(value)
        if hasattr(value, "model_dump"):
            return dict(value.model_dump())
        return {
            key: getattr(value, key)
            for key in (
                "claim_id", "id", "text", "claim", "subject", "research_question",
                "conclusion", "methodology", "population", "time_period",
                "evidence_ids", "supporting_evidence", "contradicting_evidence",
                "metadata",
            )
            if hasattr(value, key)
        }

    @staticmethod
    def _string(value: Any) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


def _mapping(item: Mapping[str, Any] | Any) -> dict[str, Any]:
    if isinstance(item, Mapping):
        return dict(item)
    if hasattr(item, "model_dump"):
        return dict(item.model_dump(mode="json"))
    return dict(item)


def format_contradictions_context(
    analyses: Sequence[Mapping[str, Any] | Any],
    *,
    limit: int = 12,
) -> str:
    """Render unresolved disagreements for the writer without choosing a side."""
    entries = [_mapping(item) for item in analyses][:limit]
    if not entries:
        return ""
    lines = [
        "CONTRADICTION ANALYSIS (unresolved; preserve both claims and evidence):",
        "Explain disagreements transparently. Do not declare one claim correct "
        "and do not prefer the newest source.",
    ]
    for item in entries:
        category = item.get("category")
        category = getattr(category, "value", category)
        lines.append(
            f"- [{category}] {item.get('claim_a_text') or item.get('claim_a_id')} "
            f"<> {item.get('claim_b_text') or item.get('claim_b_id')}"
        )
        if item.get("description"):
            lines.append(f"  {str(item['description']).replace(chr(10), ' ')[:1000]}")
        for difference in item.get("differences", [])[:4]:
            lines.append(
                f"  Difference ({difference.get('field')}): "
                f"{difference.get('claim_a_value')} vs "
                f"{difference.get('claim_b_value')}"
            )
    return "\n".join(lines)


def format_contradictions_appendix(
    analyses: Sequence[Mapping[str, Any] | Any],
) -> str:
    """Render a transparent contradiction appendix for a saved report."""
    entries = [_mapping(item) for item in analyses]
    if not entries:
        return ""
    lines = [
        "\n\n---\n",
        "## Contradiction Analysis\n",
        "> These are unresolved candidate disagreements, not verdicts. Both "
        "claims and both evidence sets are preserved; no source is discarded "
        "and recency is not treated as correctness.\n\n",
        "| Category | Claim A | Claim B | Explanation | Evidence sides |\n",
        "|---|---|---|---|---|\n",
    ]
    for item in entries:
        category = item.get("category")
        category = getattr(category, "value", category)
        evidence = (
            f"A: {', '.join(item.get('claim_a_evidence_ids', [])[:4]) or 'none'}; "
            f"B: {', '.join(item.get('claim_b_evidence_ids', [])[:4]) or 'none'}"
        )
        lines.append(
            f"| {_escape(str(category))} "
            f"| {_escape(str(item.get('claim_a_text') or item.get('claim_a_id')))} "
            f"| {_escape(str(item.get('claim_b_text') or item.get('claim_b_id')))} "
            f"| {_escape(str(item.get('description') or ''))} "
            f"| {_escape(evidence)} |\n"
        )
    lines.append(
        "\n**Status:** unresolved / requires human review. The system does not "
        "use an LLM opinion to settle scientific disagreements.\n"
    )
    return "".join(lines)


def _escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")[:700]


__all__ = [
    "ContradictionDetectionService",
    "format_contradictions_appendix",
    "format_contradictions_context",
]
