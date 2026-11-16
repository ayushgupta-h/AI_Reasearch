"""
app/services/research_completeness.py
======================================
Research completeness evaluator.

Determines whether the research adequately answers the original question by:

1. Decomposing the research question into atomic objectives.
2. Evaluating each objective's coverage from verified claims and evidence.
3. Identifying research gaps (missing evidence, weak conclusions, conflicts).
4. Generating bounded additional-research tasks for important gaps.
5. Persisting everything to PostgreSQL.

The evaluator never claims completeness without evidence: an objective is only
``complete`` when at least one verified (supported) claim covers it, and the
overall status is ``complete`` only when every objective is complete.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence

from app.config import Settings, get_settings
from app.observability import get_logger
from app.repositories.completeness import (
    CompletenessRepository,
    NullCompletenessRepository,
    build_completeness_repository,
)
from app.schemas.enums import ClaimStatus, CompletenessStatus, ObjectiveStatus

logger = get_logger("app.services.research_completeness")

# Maximum additional-research iterations the evaluator may trigger.
# Prevents infinite research loops.
DEFAULT_MAX_ADDITIONAL_ITERATIONS = 2


# ─── Data structures ──────────────────────────────────────────────────────


@dataclass
class ResearchObjectiveView:
    """One decomposed objective with its coverage assessment."""

    objective_id: str
    description: str
    required_evidence: Optional[str]
    status: str  # ObjectiveStatus value
    related_claim_ids: List[str] = field(default_factory=list)
    related_source_ids: List[str] = field(default_factory=list)
    evidence_quote_count: int = 0
    supported_claim_count: int = 0
    contradicted_claim_count: int = 0
    unverified_claim_count: int = 0


@dataclass
class CompletenessResult:
    """Result of one completeness evaluation."""

    session_id: str
    overall_status: str  # CompletenessStatus value
    completeness_score: float  # 0.0 - 100.0
    objectives: List[ResearchObjectiveView] = field(default_factory=list)
    gaps: List[Dict[str, Any]] = field(default_factory=list)
    recommendations: List[Dict[str, Any]] = field(default_factory=list)
    additional_search_tasks: List[Dict[str, Any]] = field(default_factory=list)
    iterations_performed: int = 0
    max_iterations: int = DEFAULT_MAX_ADDITIONAL_ITERATIONS


class ResearchCompletenessEvaluator:
    """
    Evaluates research completeness against the original question.

    The evaluator is deterministic given its inputs: it never invents
    evidence, never upgrades an unverified claim to supported, and never
    declares an objective complete without at least one verified claim.
    """

    def __init__(
        self,
        settings: Optional[Settings] = None,
        repository: Optional[CompletenessRepository | NullCompletenessRepository] = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._repo = repository or build_completeness_repository(self._settings)

    # ── Public API ──────────────────────────────────────────────────────

    def evaluate(
        self,
        *,
        session_id: str,
        topic: str,
        claims: Sequence[Mapping[str, Any]],
        verification_results: Sequence[Mapping[str, Any]],
        contradictions: Sequence[Mapping[str, Any]],
        sources: Sequence[Mapping[str, Any]],
        max_additional_iterations: int = DEFAULT_MAX_ADDITIONAL_ITERATIONS,
        persist: bool = True,
    ) -> CompletenessResult:
        """
        Run a full completeness evaluation for a research session.

        Parameters
        ----------
        session_id:
            Research session UUID.
        topic:
            Original research question.
        claims:
            Extracted claims from the claims_node.
        verification_results:
            Claim verification results (status per claim).
        contradictions:
            Detected contradictions.
        sources:
            Retrieved sources.
        max_additional_iterations:
            Upper bound on additional-research iterations (prevents loops).
        persist:
            Whether to write objectives/evaluation to PostgreSQL.

        Returns
        -------
        CompletenessResult
            Per-objective statuses, overall status, gaps, and bounded
            additional-search tasks.
        """
        # ── Step 1: Decompose the research question into objectives ─────
        objectives = self._decompose_objectives(topic)

        # ── Step 2: Index claims and verification by claim_id ───────────
        verification_map: Dict[str, Mapping[str, Any]] = {}
        for vr in verification_results:
            cid = vr.get("claim_id")
            if cid:
                verification_map[cid] = vr

        claim_map: Dict[str, Mapping[str, Any]] = {}
        for claim in claims:
            cid = claim.get("claim_id") or claim.get("id")
            if cid:
                claim_map[cid] = claim

        # ── Step 3: Evaluate each objective's coverage ─────────────────
        evaluated: List[ResearchObjectiveView] = []
        for obj in objectives:
            status, related_claims, related_sources = self._evaluate_objective(
                obj, claim_map, verification_map, claims, sources
            )
            evaluated.append(
                ResearchObjectiveView(
                    objective_id=obj["objective_id"],
                    description=obj["description"],
                    required_evidence=obj.get("required_evidence"),
                    status=status,
                    related_claim_ids=[c.get("claim_id", "") for c in related_claims],
                    related_source_ids=[
                        s.get("source_id", "") for s in related_sources
                    ],
                    evidence_quote_count=sum(
                        len(c.get("supporting_evidence", []))
                        + len(c.get("contradicting_evidence", []))
                        for c in related_claims
                    ),
                    supported_claim_count=sum(
                        1
                        for c in related_claims
                        if verification_map.get(c.get("claim_id", ""), {}).get("status")
                        == ClaimStatus.SUPPORTED.value
                    ),
                    contradicted_claim_count=sum(
                        1
                        for c in related_claims
                        if verification_map.get(c.get("claim_id", ""), {}).get("status")
                        == ClaimStatus.CONTRADICTED.value
                    ),
                    unverified_claim_count=sum(
                        1
                        for c in related_claims
                        if verification_map.get(c.get("claim_id", ""), {}).get("status")
                        in (
                            ClaimStatus.INSUFFICIENT_CONTEXT.value,
                            ClaimStatus.UNVERIFIED.value,
                        )
                    ),
                )
            )

        # ── Step 4: Compute overall completeness ────────────────────────
        result = self._compute_overall(
            evaluated,
            contradictions,
            max_additional_iterations,
        )

        # ── Step 5: Persist ─────────────────────────────────────────────
        if persist and self._repo.available:
            self._repo.save_objectives(
                session_id=session_id,
                objectives=[
                    {
                        "objective_id": o.objective_id,
                        "description": o.description,
                        "required_evidence": o.required_evidence,
                        "status": o.status,
                        "related_claim_ids": o.related_claim_ids,
                        "related_source_ids": o.related_source_ids,
                    }
                    for o in result.objectives
                ],
            )
            self._repo.save_evaluation(
                session_id=session_id,
                overall_status=result.overall_status,
                completeness_score=result.completeness_score,
                total_objectives=len(result.objectives),
                complete_objectives=sum(
                    1 for o in result.objectives
                    if o.status == ObjectiveStatus.COMPLETE.value
                ),
                partial_objectives=sum(
                    1 for o in result.objectives
                    if o.status == ObjectiveStatus.PARTIALLY_COMPLETE.value
                ),
                insufficient_objectives=sum(
                    1 for o in result.objectives
                    if o.status == ObjectiveStatus.INSUFFICIENT_EVIDENCE.value
                ),
                not_investigated_objectives=sum(
                    1 for o in result.objectives
                    if o.status == ObjectiveStatus.NOT_INVESTIGATED.value
                ),
                blocked_objectives=sum(
                    1 for o in result.objectives
                    if o.status == ObjectiveStatus.BLOCKED.value
                ),
                gaps=result.gaps,
                recommendations=result.recommendations,
                additional_search_tasks=result.additional_search_tasks,
                iterations_performed=result.iterations_performed,
                max_iterations=result.max_iterations,
            )

        return result

    # ── Step 1: Objective decomposition ────────────────────────────────

    def _decompose_objectives(self, topic: str) -> List[Dict[str, Any]]:
        """
        Break the research question into atomic objectives.

        Uses deterministic heuristics (question words, conjunctive splits)
        rather than an LLM, so decomposition is reproducible. Each objective
        gets a unique ``objective_id`` and a plain-language description.
        """
        if not topic or not topic.strip():
            return [
                {
                    "objective_id": "obj-1",
                    "description": topic or "Research the given topic.",
                    "required_evidence": "Any relevant, verifiable source material.",
                }
            ]

        # Split on question marks and conjunctions that suggest sub-questions
        parts = re.split(
            r"\?\s+|\band\b|\bbut\b|\bhowever\b|\bor\b", topic, flags=re.IGNORECASE
        )
        objectives: List[Dict[str, Any]] = []
        index = 1
        for part in parts:
            part = part.strip(" .,;:-").strip()
            if len(part) < 5:
                continue
            # Skip pure conjunction fragments
            if part.lower() in {"and", "but", "or", "however"}:
                continue
            objectives.append(
                {
                    "objective_id": f"obj-{index}",
                    "description": self._normalize_description(part),
                    "required_evidence": (
                        f"Verified sources that address: {part[:120]}"
                    ),
                }
            )
            index += 1

        # Always guarantee at least one objective
        if not objectives:
            objectives.append(
                {
                    "objective_id": "obj-1",
                    "description": topic,
                    "required_evidence": "Verified sources relevant to the topic.",
                }
            )

        return objectives

    @staticmethod
    def _normalize_description(text: str) -> str:
        """Clean up a decomposed objective description."""
        text = re.sub(r"\s+", " ", text).strip()
        if text and not text.endswith((".", "?", "!")):
            text += "."
        return text

    # ── Step 3: Per-objective coverage evaluation ──────────────────────

    def _evaluate_objective(
        self,
        objective: Dict[str, Any],
        claim_map: Dict[str, Mapping[str, Any]],
        verification_map: Dict[str, Mapping[str, Any]],
        all_claims: Sequence[Mapping[str, Any]],
        all_sources: Sequence[Mapping[str, Any]],
    ) -> tuple[str, List[Mapping[str, Any]], List[Mapping[str, Any]]]:
        """
        Evaluate one objective's coverage.

        Returns (status, related_claims, related_sources).

        An objective is:
        * ``complete`` when at least one related claim is ``supported``.
        * ``partially_complete`` when related claims exist but none are
          fully supported (some partially supported, rest unverified).
        * ``insufficient_evidence`` when related claims exist but all are
          unverified or insufficient context.
        * ``not_investigated`` when no claims relate to the objective.
        * ``blocked`` when related claims are contradicted with no support.
        """
        desc = objective.get("description", "").lower()
        related_claims: List[Mapping[str, Any]] = []
        related_sources: List[Mapping[str, Any]] = []

        # Match claims to the objective by keyword overlap
        for claim in all_claims:
            claim_text = claim.get("text", claim.get("claim_text", "")).lower()
            if not claim_text:
                continue
            overlap = self._keyword_overlap(desc, claim_text)
            if overlap >= 2:  # require at least two shared significant terms
                cid = claim.get("claim_id") or claim.get("id")
                if cid:
                    related_claims.append(claim)
                    # Collect source URLs from evidence
                    for ev in claim.get("supporting_evidence", []) + claim.get(
                        "contradicting_evidence", []
                    ):
                        url = ev.get("url")
                        if url and url not in [s.get("url") for s in related_sources]:
                            related_sources.append(
                                {
                                    "source_id": ev.get("source_id"),
                                    "url": url,
                                    "title": ev.get("source_title", ""),
                                }
                            )

        # Determine status from verification results
        if not related_claims:
            return ObjectiveStatus.NOT_INVESTIGATED.value, [], []

        supported = sum(
            1
            for c in related_claims
            if verification_map.get(c.get("claim_id", ""), {}).get("status")
            == ClaimStatus.SUPPORTED.value
        )
        contradicted = sum(
            1
            for c in related_claims
            if verification_map.get(c.get("claim_id", ""), {}).get("status")
            == ClaimStatus.CONTRADICTED.value
        )
        partially = sum(
            1
            for c in related_claims
            if verification_map.get(c.get("claim_id", ""), {}).get("status")
            == ClaimStatus.PARTIALLY_SUPPORTED.value
        )
        unverified = sum(
            1
            for c in related_claims
            if verification_map.get(c.get("claim_id", ""), {}).get("status")
            in (
                ClaimStatus.INSUFFICIENT_CONTEXT.value,
                ClaimStatus.UNVERIFIED.value,
            )
        )

        if supported > 0 and contradicted == 0:
            status = ObjectiveStatus.COMPLETE.value
        elif supported > 0 and contradicted > 0:
            status = ObjectiveStatus.PARTIALLY_COMPLETE.value
        elif partially > 0 and supported == 0 and contradicted == 0:
            status = ObjectiveStatus.PARTIALLY_COMPLETE.value
        elif contradicted > 0 and supported == 0 and partially == 0:
            status = ObjectiveStatus.BLOCKED.value
        else:
            # All unverified / insufficient context
            status = ObjectiveStatus.INSUFFICIENT_EVIDENCE.value

        return status, related_claims, related_sources

    @staticmethod
    def _keyword_overlap(text_a: str, text_b: str) -> int:
        """Count shared significant (3+ char, non-stopword) terms."""
        stop = {
            "the", "and", "for", "with", "from", "that", "this", "into",
            "are", "was", "were", "has", "have", "had", "but", "not", "can",
            "could", "would", "should", "their", "there", "they", "than",
            "then", "also", "about", "over", "under", "such", "which",
            "while", "where", "when", "what", "who", "all", "any", "may",
            "might", "must", "only", "more", "most", "some", "our", "its",
            "it", "as", "at", "by", "be", "been", "is", "of", "on", "or",
            "to", "in", "a", "an",
        }
        terms_a = {
            t for t in re.findall(r"[a-z0-9]{3,}", text_a) if t not in stop
        }
        terms_b = {
            t for t in re.findall(r"[a-z0-9]{3,}", text_b) if t not in stop
        }
        return len(terms_a & terms_b)

    # ── Step 4: Overall completeness + gap identification ──────────────

    def _compute_overall(
        self,
        objectives: List[ResearchObjectiveView],
        contradictions: Sequence[Mapping[str, Any]],
        max_additional_iterations: int,
    ) -> CompletenessResult:
        """Roll up per-objective statuses into an overall completeness result."""
        total = len(objectives)
        complete = sum(1 for o in objectives if o.status == ObjectiveStatus.COMPLETE.value)
        partial = sum(
            1 for o in objectives if o.status == ObjectiveStatus.PARTIALLY_COMPLETE.value
        )
        insufficient = sum(
            1 for o in objectives if o.status == ObjectiveStatus.INSUFFICIENT_EVIDENCE.value
        )
        not_investigated = sum(
            1 for o in objectives if o.status == ObjectiveStatus.NOT_INVESTIGATED.value
        )
        blocked = sum(
            1 for o in objectives if o.status == ObjectiveStatus.BLOCKED.value
        )

        # Score: complete=100, partial=50, others=0
        score = (
            (complete * 100.0 + partial * 50.0) / total
            if total > 0
            else 0.0
        )

        # Overall status: complete only if every objective is complete
        if total > 0 and complete == total and not_investigated == 0 and blocked == 0:
            overall = CompletenessStatus.COMPLETE.value
        elif total > 0 and (complete + partial) > 0:
            overall = CompletenessStatus.PARTIALLY_COMPLETE.value
        else:
            overall = CompletenessStatus.INSUFFICIENT.value

        # ── Gap identification ────────────────────────────────────────
        gaps: List[Dict[str, Any]] = []
        recommendations: List[Dict[str, Any]] = []
        additional_tasks: List[Dict[str, Any]] = []

        for obj in objectives:
            if obj.status == ObjectiveStatus.NOT_INVESTIGATED.value:
                gaps.append(
                    {
                        "objective_id": obj.objective_id,
                        "type": "missing_evidence",
                        "description": (
                            f"No claims address the objective: {obj.description}"
                        ),
                        "severity": "high",
                    }
                )
                recommendations.append(
                    {
                        "objective_id": obj.objective_id,
                        "action": "search",
                        "description": (
                            f"Retrieve sources addressing: {obj.description}"
                        ),
                    }
                )
                additional_tasks.append(
                    {
                        "objective_id": obj.objective_id,
                        "query": obj.description,
                        "source_types": ["academic", "web"],
                        "priority": "high",
                    }
                )
            elif obj.status == ObjectiveStatus.INSUFFICIENT_EVIDENCE.value:
                gaps.append(
                    {
                        "objective_id": obj.objective_id,
                        "type": "insufficient_evidence",
                        "description": (
                            f"Claims exist but lack verified support: "
                            f"{obj.description}"
                        ),
                        "severity": "medium",
                    }
                )
                recommendations.append(
                    {
                        "objective_id": obj.objective_id,
                        "action": "search",
                        "description": (
                            f"Find additional supporting evidence for: "
                            f"{obj.description}"
                        ),
                    }
                )
                additional_tasks.append(
                    {
                        "objective_id": obj.objective_id,
                        "query": obj.description,
                        "source_types": ["academic", "web"],
                        "priority": "medium",
                    }
                )
            elif obj.status == ObjectiveStatus.BLOCKED.value:
                gaps.append(
                    {
                        "objective_id": obj.objective_id,
                        "type": "contradicted",
                        "description": (
                            f"Related claims are contradicted with no support: "
                            f"{obj.description}"
                        ),
                        "severity": "high",
                    }
                )
                recommendations.append(
                    {
                        "objective_id": obj.objective_id,
                        "action": "review",
                        "description": (
                            f"Resolve contradicting evidence for: "
                            f"{obj.description}"
                        ),
                    }
                )

        # Contradictions are also gaps
        for contr in contradictions[:5]:
            gaps.append(
                {
                    "type": "contradiction",
                    "description": contr.get("description", "Unresolved claim disagreement"),
                    "severity": str(contr.get("severity", "medium")),
                    "claim_a_id": contr.get("claim_a_id"),
                    "claim_b_id": contr.get("claim_b_id"),
                }
            )
            recommendations.append(
                {
                    "action": "resolve_contradiction",
                    "description": (
                        "Review and reconcile conflicting evidence before "
                        "treating either claim as established."
                    ),
                }
            )

        # Bounded additional research
        iterations = min(len(additional_tasks), max_additional_iterations)
        bounded_tasks = additional_tasks[: max_additional_iterations]

        return CompletenessResult(
            session_id="",  # set by caller
            overall_status=overall,
            completeness_score=round(score, 2),
            objectives=objectives,
            gaps=gaps,
            recommendations=recommendations,
            additional_search_tasks=bounded_tasks,
            iterations_performed=iterations,
            max_iterations=max_additional_iterations,
        )


__all__ = [
    "ResearchCompletenessEvaluator",
    "CompletenessResult",
    "ResearchObjectiveView",
    "DEFAULT_MAX_ADDITIONAL_ITERATIONS",
]
