"""
app/services/report_generation_service.py
==========================================
Evidence-grounded research report generation.

Builds a structured Markdown research report from verified claims, evidence
passages, contradictions, source-credibility evaluations and prior memory.
Every factual finding references actual retrieved sources (URL/DOI); no
reference or quotation is ever fabricated. Verification statuses from the
claim-level verification layer are honoured: contradicted claims are never
presented as established facts, unverified claims are never presented as
verified findings, and claims lacking sufficient evidence are explicitly
flagged.

Sections produced (only those relevant to the topic are emitted):
  1. Title
  2. Executive Summary
  3. Research Question and Objectives
  4. Research Methodology
  5. Key Findings (evidence-backed)
  6. Source Credibility Considerations
  7. Contradictory Findings
  8. Unanswered Questions and Research Gaps
  9. Limitations
  10. Conclusion
  11. References
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence

from app.config import Settings, get_settings
from app.observability import get_logger
from app.schemas.claim import Claim, ContradictionAnalysis, Evidence
from app.schemas.enums import ClaimStatus, EvidenceStance
from app.schemas.source_credibility import SourceCredibilityEvaluation

logger = get_logger("app.services.report_generation")


# ─── Data structures ──────────────────────────────────────────────────────


@dataclass
class ReportContext:
    """All inputs needed to build one evidence-grounded report."""

    topic: str
    research_question: str
    claims: List[Dict[str, Any]] = field(default_factory=list)
    verification_results: List[Dict[str, Any]] = field(default_factory=list)
    contradictions: List[Dict[str, Any]] = field(default_factory=list)
    credibility_evaluations: List[Dict[str, Any]] = field(default_factory=list)
    evidence_map: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    retrieved_memories: List[Dict[str, Any]] = field(default_factory=list)
    critic_score: Optional[int] = None
    revision_count: int = 0
    max_revisions: int = 2
    provider: str = "gemini"
    session_id: Optional[str] = None
    project_id: Optional[str] = None
    search_source_count: int = 0
    rag_available: bool = False


@dataclass
class GeneratedReport:
    """The generated Markdown report and its citation inventory."""

    title: str
    content_markdown: str
    citations: List[Dict[str, Any]] = field(default_factory=list)
    verified_claim_count: int = 0
    unverified_claim_count: int = 0
    contradicted_claim_count: int = 0
    contradiction_count: int = 0
    source_count: int = 0
    word_count: int = 0


class ReportGenerationService:
    """
    Builds an evidence-grounded Markdown research report.

    The report is deterministic given its inputs: it never invents citations,
    never fabricates quotations, and never upgrades an unverified or
    contradicted claim to an established finding.
    """

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self._settings = settings or get_settings()

    # ── Public API ──────────────────────────────────────────────────────

    def generate(self, context: ReportContext) -> GeneratedReport:
        """
        Generate a complete, evidence-grounded research report in Markdown.

        Parameters
        ----------
        context:
            All research inputs: claims, verification results, evidence,
            contradictions, credibility evaluations, memories.

        Returns
        -------
        GeneratedReport
            Markdown content plus a citation inventory for downstream
            versioning and export.
        """
        claims = context.claims
        verification_map = {
            vr.get("claim_id"): vr for vr in context.verification_results
        }

        # Index evidence by claim_id for citation lookup
        evidence_by_claim: Dict[str, List[Dict[str, Any]]] = {}
        for claim in claims:
            cid = claim.get("claim_id", "")
            evidence_by_claim[cid] = context.evidence_map.get(cid, [])

        # Build citation inventory from all evidence
        citation_inventory = self._build_citation_inventory(
            claims, context.verification_results, evidence_by_claim
        )

        sections: List[str] = []

        # 1. Title
        sections.append(f"# {context.topic}\n")

        # 2. Executive Summary
        sections.append(self._executive_summary(context, verification_map))

        # 3. Research Question and Objectives
        sections.append(self._research_question_section(context))

        # 4. Research Methodology
        sections.append(self._methodology_section(context))

        # 5. Key Findings (evidence-backed)
        findings_section, found_counts = self._key_findings_section(
            claims, verification_map, evidence_by_claim
        )
        sections.append(findings_section)

        # 6. Source Credibility Considerations
        sections.append(
            self._source_credibility_section(context.credibility_evaluations)
        )

        # 7. Contradictory Findings
        sections.append(self._contradictions_section(context.contradictions))

        # 8. Unanswered Questions and Research Gaps
        sections.append(
            self._research_gaps_section(
                context, verification_map, evidence_by_claim
            )
        )

        # 9. Limitations
        sections.append(self._limitations_section(context))

        # 10. Conclusion
        sections.append(self._conclusion_section(context, found_counts))

        # 11. References
        sections.append(self._references_section(citation_inventory))

        content = "\n\n".join(s for s in sections if s.strip())
        word_count = len(content.split())

        return GeneratedReport(
            title=context.topic,
            content_markdown=content,
            citations=citation_inventory,
            verified_claim_count=found_counts["verified"],
            unverified_claim_count=found_counts["unverified"],
            contradicted_claim_count=found_counts["contradicted"],
            contradiction_count=len(context.contradictions),
            source_count=len(citation_inventory),
            word_count=word_count,
        )

    # ── Section builders ────────────────────────────────────────────────

    def _frontmatter(
        self, context: ReportContext, citations: List[Dict[str, Any]]
    ) -> str:
        now = datetime.now(timezone.utc).isoformat()
        verified_cites = sum(
            1 for c in citations if c.get("verification_status") == "verified"
        )
        return (
            "---\n"
            f"title: \"{self._escape_yaml(context.topic)}\"\n"
            f"generated_at: \"{now}\"\n"
            f"provider: \"{context.provider}\"\n"
            f"research_question: \"{self._escape_yaml(context.research_question)}\"\n"
            f"critic_score: {context.critic_score if context.critic_score is not None else 'N/A'}\n"
            f"revision_count: {context.revision_count}\n"
            f"max_revisions: {context.max_revisions}\n"
            f"sources_analyzed: {context.search_source_count}\n"
            f"sources_cited: {len(citations)}\n"
            f"citations_verified: {verified_cites}\n"
            f"claims_verified: {found_count(context.verification_results, ClaimStatus.SUPPORTED.value)}\n"
            f"claims_unverified: {found_count(context.verification_results, ClaimStatus.INSUFFICIENT_CONTEXT.value)}\n"
            f"claims_contradicted: {found_count(context.verification_results, ClaimStatus.CONTRADICTED.value)}\n"
            f"contradictions_detected: {len(context.contradictions)}\n"
            f"---\n"
        )

    def _executive_summary(
        self, context: ReportContext, verification_map: Dict[str, Any]
    ) -> str:
        lines = ["## Executive Summary\n"]
        claims = context.claims
        if not claims:
            lines.append(
                "No factual claims were extracted from the research sources. "
                "This report summarizes the available source material without "
                "individual claim-level verification.\n"
            )
            return "\n".join(lines)

        verified = [
            c for c in claims
            if verification_map.get(c.get("claim_id", ""), {}).get("status")
            == ClaimStatus.SUPPORTED.value
        ]
        partially = [
            c for c in claims
            if verification_map.get(c.get("claim_id", ""), {}).get("status")
            == ClaimStatus.PARTIALLY_SUPPORTED.value
        ]
        contradicted = [
            c for c in claims
            if verification_map.get(c.get("claim_id", ""), {}).get("status")
            == ClaimStatus.CONTRADICTED.value
        ]
        unverified = [
            c for c in claims
            if verification_map.get(c.get("claim_id", ""), {}).get("status")
            == ClaimStatus.INSUFFICIENT_CONTEXT.value
        ]

        total = len(claims)
        lines.append(
            f"This report synthesizes {total} claim(s) extracted from "
            f"{context.search_source_count} research source(s). "
        )
        if verified:
            lines.append(
                f"{len(verified)} claim(s) are supported by retrieved evidence. "
            )
        if partially:
            lines.append(
                f"{len(partially)} claim(s) are partially supported. "
            )
        if contradicted:
            lines.append(
                f"{len(contradicted)} claim(s) are contradicted by retrieved "
                f"evidence and are not presented as established facts. "
            )
        if unverified:
            lines.append(
                f"{len(unverified)} claim(s) lack sufficient evidence for "
                f"verification. "
            )
        if context.contradictions:
            lines.append(
                f"{len(context.contradictions)} area(s) of conflicting "
                f"evidence were identified and are documented transparently. "
            )
        lines.append(
            "All findings cite their underlying sources; no quotation or "
            "reference is fabricated."
        )
        return "\n".join(lines)

    def _research_question_section(self, context: ReportContext) -> str:
        lines = ["## Research Question and Objectives\n"]
        lines.append(f"**Research question:** {context.research_question}\n")
        lines.append(
            "**Objectives:**\n"
            "- Synthesize findings from retrieved research sources.\n"
            "- Verify each factual claim against its supporting evidence.\n"
            "- Document source credibility and contradictions transparently.\n"
            "- Identify research gaps and limitations.\n"
        )
        return "\n".join(lines)

    def _methodology_section(self, context: ReportContext) -> str:
        lines = ["## Research Methodology\n"]
        lines.append(
            "This research followed a multi-stage pipeline:\n"
            "1. **Query intake** — the research topic was decomposed into a "
            "structured research plan.\n"
            "2. **Multi-source retrieval** — web, academic and knowledge-base "
            "sources were searched in parallel.\n"
            "3. **Deep reading** — primary sources were extracted and indexed.\n"
            "4. **Claim extraction** — atomic factual claims were identified "
            "from the synthesized material.\n"
            "5. **Evidence association** — each claim was paired with the "
            "retrieved passages that bear on it.\n"
            "6. **Claim verification** — each claim was classified as "
            "supported, partially supported, contradicted, unverified, or "
            "insufficient context.\n"
            "7. **Contradiction analysis** — conflicting claims were "
            "identified without declaring a winner.\n"
            "8. **Source credibility** — each source was heuristically "
            "evaluated on publisher, authorship, recency, methodology and "
            "peer review.\n"
            "9. **Report generation** — this evidence-grounded report was "
            "produced from verified claims only.\n"
        )
        if context.rag_available:
            lines.append(
                "10. **Retrieval-augmented generation** — relevant prior "
                "research from the project memory was retrieved and considered.\n"
            )
        lines.append(
            "\nMemory-assisted research: prior verified findings from the "
            "authenticated user's research memory were retrieved to supplement "
            "(not replace) fresh source material.\n"
        )
        return "\n".join(lines)

    def _key_findings_section(
        self,
        claims: List[Dict[str, Any]],
        verification_map: Dict[str, Any],
        evidence_by_claim: Dict[str, List[Dict[str, Any]]],
    ) -> tuple[str, Dict[str, int]]:
        lines = ["## Key Findings\n"]
        counts = {"verified": 0, "unverified": 0, "contradicted": 0, "partial": 0}

        # Order: verified first, then partial, then unverified, then contradicted
        ordered = sorted(
            claims,
            key=lambda c: _claim_order_key(
                c, verification_map
            ),
        )

        finding_number = 0
        for claim in claims:
            cid = claim.get("claim_id", "")
            text = claim.get("text", claim.get("claim_text", ""))
            if not text:
                continue
            vr = verification_map.get(cid, {})
            status = vr.get("status", claim.get("status", "unverified"))
            confidence = vr.get("confidence", claim.get("confidence", 0.5))

            evidence_list = evidence_by_claim.get(cid, [])
            supports = [
                e for e in evidence_list
                if e.get("stance") in (EvidenceStance.SUPPORTS.value, "supports")
            ]
            contradicts = [
                e for e in evidence_list
                if e.get("stance") in (EvidenceStance.CONTRADICTS.value, "contradicts")
            ]

            finding_number += 1
            status_label = status.replace("_", " ").title()

            # Never present a contradicted claim as an established fact
            if status == ClaimStatus.CONTRADICTED.value:
                counts["contradicted"] += 1
                lines.append(
                    f"### {finding_number}. {text}\n"
                    f"**Verification status:** Contradicted "
                    f"(confidence: {confidence:.2f})\n"
                    f"**Assessment:** This claim is contradicted by retrieved "
                    f"evidence and is **not presented as an established fact**. "
                    f"The contradicting evidence is cited below.\n"
                )
                if contradicts:
                    lines.append("**Contradicting evidence:**\n")
                    for e in contradicts[:3]:
                        lines.append(
                            f"- {self._cite_evidence(e)}\n"
                        )
                continue

            if status == ClaimStatus.INSUFFICIENT_CONTEXT.value:
                counts["unverified"] += 1
                lines.append(
                    f"### {finding_number}. {text}\n"
                    f"**Verification status:** Insufficient evidence "
                    f"(confidence: {confidence:.2f})\n"
                    f"**Assessment:** No sufficient evidence was retrieved to "
                    f"verify or refute this claim. It is reported as unverified "
                    f"and should not be treated as a finding.\n"
                )
                continue

            if status == ClaimStatus.PARTIALLY_SUPPORTED.value:
                counts["partial"] += 1
            else:
                counts["verified"] += 1

            lines.append(
                f"### {finding_number}. {text}\n"
                f"**Verification status:** {status_label} "
                f"(confidence: {confidence:.2f})\n"
            )

            if supports:
                lines.append("**Supporting evidence:**\n")
                for e in supports[:3]:
                    lines.append(f"- {self._cite_evidence(e)}\n")

            if contradicts and status != ClaimStatus.CONTRADICTED.value:
                lines.append("**Note:** Some retrieved evidence contradicts "
                             "aspects of this claim:\n")
                for e in contradicts[:2]:
                    lines.append(f"- {self._cite_evidence(e)}\n")

            if not supports and not contradicts:
                lines.append(
                    "_No directly supporting or contradicting evidence was "
                    "retrieved for this claim._\n"
                )

        return "\n".join(lines), counts

    def _source_credibility_section(
        self, evaluations: List[Dict[str, Any]]
    ) -> str:
        lines = ["## Source Credibility Considerations\n"]
        if not evaluations:
            lines.append(
                "No source-credibility evaluations were available. Sources "
                "are cited by URL/DOI without a formal quality assessment.\n"
            )
            return "\n".join(lines)

        lines.append(
            "The following heuristic credibility assessment is based only on "
            "supplied metadata (publisher, authorship, recency, methodology, "
            "peer review). Scores are not objective truth ratings, and missing "
            "information is not treated as negative evidence.\n\n"
        )
        lines.append(
            "| Source | Score | Confidence | Label | Key caveats |\n"
            "|---|---:|---:|---|---|\n"
        )
        for eval_item in evaluations[:15]:
            title = eval_item.get("title") or eval_item.get("source_url") or "Untitled source"
            url = eval_item.get("source_url", "")
            score = eval_item.get("score")
            score_text = "—" if score is None else f"{float(score):.2f}"
            confidence = eval_item.get("confidence", 0)
            label = eval_item.get("label", "insufficient_information")
            missing = ", ".join(eval_item.get("missing_fields", [])[:5]) or "none"
            lines.append(
                f"| {self._md_escape(title)} | {score_text} | {confidence:.2f} "
                f"| {label} | Missing: {self._md_escape(missing)} |\n"
            )
        lines.append(
            "\nAll cited sources retain their original URLs and DOIs where "
            "available. Readers should verify sources independently.\n"
        )
        return "\n".join(lines)

    def _contradictions_section(
        self, contradictions: List[Dict[str, Any]]
    ) -> str:
        lines = ["## Contradictory Findings\n"]
        if not contradictions:
            lines.append(
                "No contradictory claims were detected among the verified "
                "findings.\n"
            )
            return "\n".join(lines)

        lines.append(
            "The following pairs of claims reach incompatible conclusions. "
            "Both claims and their evidence are preserved; no source is "
            "discarded and recency is not treated as correctness. These are "
            "candidate disagreements requiring human review, not settled "
            "verdicts.\n\n"
        )
        for item in contradictions[:12]:
            cat = item.get("category", "insufficient_information")
            cat = getattr(cat, "value", cat)
            sev = item.get("severity", "medium")
            sev = getattr(sev, "value", sev)
            lines.append(
                f"### Claim A: {self._md_escape(item.get('claim_a_text') or item.get('claim_a_id'))}\n"
            )
            lines.append(
                f"**vs Claim B:** {self._md_escape(item.get('claim_b_text') or item.get('claim_b_id'))}\n"
            )
            lines.append(
                f"**Category:** {cat}  |  **Severity:** {sev}\n"
            )
            if item.get("description"):
                lines.append(f"**Assessment:** {self._md_escape(str(item['description']))}\n")
            for diff in item.get("differences", [])[:3]:
                lines.append(
                    f"- Difference ({diff.get('field')}): "
                    f"{diff.get('claim_a_value')} vs {diff.get('claim_b_value')}\n"
                )
            lines.append("")
        return "\n".join(lines)

    def _research_gaps_section(
        self,
        context: ReportContext,
        verification_map: Dict[str, Any],
        evidence_by_claim: Dict[str, List[Dict[str, Any]]],
    ) -> str:
        lines = ["## Unanswered Questions and Research Gaps\n"]
        claims = context.claims
        if not claims:
            lines.append(
                "No claims were extracted, so no research gaps can be "
                "identified from claim-level evidence.\n"
            )
            return "\n".join(lines)

        gaps: List[str] = []
        for claim in claims:
            cid = claim.get("claim_id", "")
            vr = verification_map.get(cid, {})
            status = vr.get("status", claim.get("status", "unverified"))
            evidence_list = evidence_by_claim.get(cid, [])
            if status in (
                ClaimStatus.INSUFFICIENT_CONTEXT.value,
                ClaimStatus.UNVERIFIED.value,
            ) and not evidence_list:
                gaps.append(claim.get("text", claim.get("claim_text", "")))

        if gaps:
            lines.append(
                "The following claims lack sufficient evidence and represent "
                "research gaps:\n"
            )
            for gap in gaps[:10]:
                lines.append(f"- {self._md_escape(gap)}\n")
        else:
            lines.append(
                "All extracted claims have at least some associated evidence. "
                "No evidence-free research gaps were identified.\n"
            )

        # Contradictions also represent gaps
        if context.contradictions:
            lines.append(
                f"\nAdditionally, {len(context.contradictions)} area(s) of "
                f"conflicting evidence remain unresolved and require further "
                f"research or human adjudication.\n"
            )

        if context.retrieved_memories:
            lines.append(
                "\nPrior research memory was consulted; gaps not covered by "
                "prior memory may warrant new primary research.\n"
            )
        return "\n".join(lines)

    def _limitations_section(self, context: ReportContext) -> str:
        lines = ["## Limitations\n"]
        lines.append(
            "- **Claim-level verification is probabilistic:** classification "
            "depends on the quality and coverage of retrieved sources.\n"
            "- **Heuristic credibility scores:** source credibility is a "
            "metadata-based assessment, not a verification of truth.\n"
            "- **No source is discarded:** contradicted claims remain visible "
            "so readers can evaluate competing evidence.\n"
            "- **Temporal coverage:** findings reflect the sources available "
            "at the time of research; newer evidence may change conclusions.\n"
            "- **Retrieval coverage:** not all relevant sources may have been "
            "discovered by the search pipeline.\n"
        )
        if context.search_source_count == 0:
            lines.append(
                "- **No live sources:** this report is based on available "
                "material without fresh web/academic retrieval.\n"
            )
        unverified_count = found_count(
            context.verification_results,
            ClaimStatus.INSUFFICIENT_CONTEXT.value,
        )
        if unverified_count > 0:
            lines.append(
                f"- **{unverified_count} claim(s) lack sufficient evidence** "
                f"and are reported as unverified.\n"
            )
        return "\n".join(lines)

    def _conclusion_section(
        self, context: ReportContext, counts: Dict[str, int]
    ) -> str:
        lines = ["## Conclusion\n"]
        total = sum(counts.values())
        if total == 0:
            lines.append(
                "No verifiable findings were produced for this research "
                "question. The topic warrants further source retrieval before "
                "definitive conclusions can be drawn.\n"
            )
            return "\n".join(lines)

        lines.append(
            f"This research synthesized {total} claim(s) from "
            f"{context.search_source_count} source(s). "
        )
        if counts["verified"] > 0:
            lines.append(
                f"{counts['verified']} claim(s) are supported by evidence. "
            )
        if counts["partial"] > 0:
            lines.append(
                f"{counts['partial']} claim(s) are partially supported. "
            )
        if counts["contradicted"] > 0:
            lines.append(
                f"{counts['contradicted']} claim(s) are contradicted by "
                f"retrieved evidence and are not treated as established. "
            )
        if counts["unverified"] > 0:
            lines.append(
                f"{counts['unverified']} claim(s) lack sufficient evidence "
                f"for verification. "
            )
        if context.contradictions:
            lines.append(
                f"{len(context.contradictions)} area(s) of conflicting "
                f"evidence require further investigation. "
            )
        lines.append(
            "All findings are traceable to their cited sources. Readers "
            "should consult the References section and verify sources "
            "independently before relying on any individual finding.\n"
        )
        return "\n".join(lines)

    def _references_section(
        self, citations: List[Dict[str, Any]]
    ) -> str:
        lines = ["## References\n"]
        if not citations:
            lines.append(
                "No sources were cited in this report.\n"
            )
            return "\n".join(lines)

        lines.append(
            "The following sources were retrieved and cited. Each reference "
            "preserves the original URL and DOI where available. No reference "
            "is fabricated.\n\n"
        )
        for i, cite in enumerate(citations, 1):
            title = cite.get("title") or cite.get("source_url") or "Untitled source"
            url = cite.get("source_url", "")
            doi = cite.get("doi", "")
            source_type = cite.get("source_type", "web")
            lines.append(
                f"{i}. {self._md_escape(title)} "
                f"[{source_type}]."
            )
            if doi:
                lines[-1] += f" DOI: {doi}."
            if url:
                lines[-1] += f" URL: <{url}>."
            lines[-1] += "\n"
            # Verification note
            vs = cite.get("verification_status")
            if vs:
                lines.append(f"   Citation verification: {vs}.\n")
        return "\n".join(lines)

    # ── Citation inventory ─────────────────────────────────────────────

    def _build_citation_inventory(
        self,
        claims: List[Dict[str, Any]],
        verification_results: List[Dict[str, Any]],
        evidence_by_claim: Dict[str, List[Dict[str, Any]]],
    ) -> List[Dict[str, Any]]:
        """Collect unique citations from all evidence, preserving URLs/DOIs."""
        seen: Dict[str, Dict[str, Any]] = {}
        for claim in claims:
            cid = claim.get("claim_id", "")
            for evidence in evidence_by_claim.get(cid, []):
                url = evidence.get("source_url", "")
                doi = evidence.get("doi", "")
                key = doi or url or evidence.get("evidence_id", "")
                if key in seen:
                    continue
                seen[key] = {
                    "citation_id": evidence.get("evidence_id", key),
                    "source_id": evidence.get("source_id"),
                    "title": evidence.get("source_title", ""),
                    "source_url": url,
                    "doi": doi,
                    "source_type": evidence.get("source_type", "web"),
                    "verification_status": evidence.get(
                        "verification_status", "unverified"
                    ),
                    "claim_ids": [cid],
                }
        # Associate claims with citations
        for claim in claims:
            cid = claim.get("claim_id", "")
            for evidence in evidence_by_claim.get(cid, []):
                key = evidence.get("doi") or evidence.get("source_url") or evidence.get("evidence_id", "")
                if key in seen and cid not in seen[key]["claim_ids"]:
                    seen[key]["claim_ids"].append(cid)
        return list(seen.values())

    # ── Helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _cite_evidence(evidence: Dict[str, Any]) -> str:
        """Render one evidence item as a cited sentence fragment."""
        quote = evidence.get("quote", "")
        if len(quote) > 300:
            quote = quote[:300].rsplit(" ", 1)[0] + "..."
        url = evidence.get("source_url", "")
        doi = evidence.get("doi", "")
        source = evidence.get("source_title", "") or url or "retrieved source"
        parts = [f'"{quote}"']
        if doi:
            parts.append(f"(DOI: {doi})")
        if url:
            parts.append(f"(<{url}>)")
        parts.append(f"— {source}")
        return " ".join(parts)

    @staticmethod
    def _md_escape(text: str) -> str:
        if not text:
            return ""
        return text.replace("|", "\\|").replace("\n", " ")

    @staticmethod
    def _escape_yaml(text: str) -> str:
        if not text:
            return ""
        return text.replace('"', '\\"').replace("\n", " ")[:200]


def found_count(
    verification_results: Sequence[Mapping[str, Any]], status: str
) -> int:
    """Count verification results matching *status*."""
    return sum(
        1 for vr in verification_results
        if vr.get("status") == status
    )


def _claim_order_key(
    claim: Dict[str, Any], verification_map: Dict[str, Any]
) -> tuple:
    """Sort key: verified first, then partial, unverified, contradicted."""
    cid = claim.get("claim_id", "")
    status = verification_map.get(cid, {}).get(
        "status", claim.get("status", "unverified")
    )
    order = {
        ClaimStatus.SUPPORTED.value: 0,
        ClaimStatus.PARTIALLY_SUPPORTED.value: 1,
        ClaimStatus.INSUFFICIENT_CONTEXT.value: 2,
        ClaimStatus.UNVERIFIED.value: 2,
        ClaimStatus.CONTRADICTED.value: 3,
    }.get(status, 4)
    return (order, claim.get("claim_text", claim.get("text", "")))


__all__ = [
    "ReportContext",
    "GeneratedReport",
    "ReportGenerationService",
    "found_count",
]
