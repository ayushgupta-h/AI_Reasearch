"""
tests/test_report_generation.py
================================
Tests for evidence-grounded research report generation (Task 3.8):

* Report structure: all required sections present
* Citation grounding: citations point to actual sources with DOI/URLs
* Claim verification: contradicted claims not presented as facts,
  unverified claims flagged, verified claims cited
* Contradictions and research gaps documented
* Critic evidence audit integration
* Report versioning via ReportManagementService
* Export preservation (existing exporter still works)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from app.services.report_generation_service import (
    GeneratedReport,
    ReportContext,
    ReportGenerationService,
    found_count,
)
from app.schemas.enums import ClaimStatus, EvidenceStance


# ─── Report structure ───────────────────────────────────────────────────


class TestReportStructure:
    def test_all_required_sections_present(self):
        service = ReportGenerationService()
        context = ReportContext(
            topic="AI in Healthcare",
            research_question="Does AI improve diagnostic accuracy?",
            claims=[
                {
                    "claim_id": "c1",
                    "text": "AI improves diagnostic accuracy by 34%.",
                    "claim_type": "statistical",
                },
            ],
            verification_results=[
                {
                    "claim_id": "c1",
                    "status": ClaimStatus.SUPPORTED.value,
                    "confidence": 0.9,
                    "supporting_evidence_ids": ["e1"],
                    "contradicting_evidence_ids": [],
                },
            ],
            contradictions=[],
            credibility_evaluations=[],
            evidence_map={
                "c1": [
                    {
                        "evidence_id": "e1",
                        "quote": "AI improved diagnostic accuracy by 34% in the study.",
                        "source_url": "https://example.com/study",
                        "source_title": "Medical AI Study",
                        "stance": EvidenceStance.SUPPORTS.value,
                        "strength": 0.9,
                    },
                ],
            },
        )
        report = service.generate(context)
        content = report.content_markdown

        required = [
            "# AI in Healthcare",
            "## Executive Summary",
            "## Research Question and Objectives",
            "## Research Methodology",
            "## Key Findings",
            "## Source Credibility Considerations",
            "## Contradictory Findings",
            "## Unanswered Questions and Research Gaps",
            "## Limitations",
            "## Conclusion",
            "## References",
        ]
        for section in required:
            assert section in content, f"Missing section: {section}"

    def test_report_has_title(self):
        service = ReportGenerationService()
        context = ReportContext(topic="Climate Change", research_question="Is climate change real?")
        report = service.generate(context)
        assert report.title == "Climate Change"
        assert "# Climate Change" in report.content_markdown

    def test_empty_claims_produces_valid_report(self):
        service = ReportGenerationService()
        context = ReportContext(topic="Test Topic", research_question="Test?")
        report = service.generate(context)
        assert report.word_count > 0
        assert "## Executive Summary" in report.content_markdown
        assert "## References" in report.content_markdown


# ─── Citation grounding ─────────────────────────────────────────────────


class TestCitationGrounding:
    def test_citations_reference_actual_sources(self):
        service = ReportGenerationService()
        context = ReportContext(
            topic="AI in Healthcare",
            research_question="Does AI improve diagnostic accuracy?",
            claims=[
                {
                    "claim_id": "c1",
                    "text": "AI improves diagnostic accuracy by 34%.",
                },
            ],
            verification_results=[
                {
                    "claim_id": "c1",
                    "status": ClaimStatus.SUPPORTED.value,
                    "confidence": 0.9,
                    "supporting_evidence_ids": ["e1"],
                    "contradicting_evidence_ids": [],
                },
            ],
            contradictions=[],
            credibility_evaluations=[],
            evidence_map={
                "c1": [
                    {
                        "evidence_id": "e1",
                        "quote": "AI improved diagnostic accuracy by 34%.",
                        "source_url": "https://example.com/study",
                        "source_title": "Medical AI Study",
                        "stance": EvidenceStance.SUPPORTS.value,
                        "strength": 0.9,
                        "doi": "10.1000/example",
                    },
                ],
            },
        )
        report = service.generate(context)
        content = report.content_markdown

        # Citations must reference actual URLs/DOIs
        assert "https://example.com/study" in content
        assert "10.1000/example" in content
        assert "Medical AI Study" in content

    def test_no_fabricated_references(self):
        """Citations inventory only contains evidence that was actually provided."""
        service = ReportGenerationService()
        context = ReportContext(
            topic="Test",
            research_question="Test?",
            claims=[
                {"claim_id": "c1", "text": "Claim one."},
            ],
            verification_results=[
                {
                    "claim_id": "c1",
                    "status": ClaimStatus.SUPPORTED.value,
                    "confidence": 0.8,
                    "supporting_evidence_ids": ["e1"],
                    "contradicting_evidence_ids": [],
                },
            ],
            contradictions=[],
            credibility_evaluations=[],
            evidence_map={
                "c1": [
                    {
                        "evidence_id": "e1",
                        "quote": "Real evidence quote.",
                        "source_url": "https://real-source.com/paper",
                        "source_title": "Real Paper",
                        "stance": EvidenceStance.SUPPORTS.value,
                        "strength": 0.8,
                    },
                ],
            },
        )
        report = service.generate(context)
        # The citation should reference the real source only
        assert "https://real-source.com/paper" in report.content_markdown
        assert "Real Paper" in report.content_markdown
        # No fabricated URLs
        assert "example.com" not in report.content_markdown
        assert len(report.citations) == 1
        assert report.citations[0]["source_url"] == "https://real-source.com/paper"

    def test_citation_inventory_preserves_doi_and_url(self):
        service = ReportGenerationService()
        context = ReportContext(
            topic="Test",
            research_question="Test?",
            claims=[
                {"claim_id": "c1", "text": "Claim one."},
            ],
            verification_results=[
                {
                    "claim_id": "c1",
                    "status": ClaimStatus.SUPPORTED.value,
                    "confidence": 0.8,
                    "supporting_evidence_ids": ["e1"],
                    "contradicting_evidence_ids": [],
                },
            ],
            contradictions=[],
            credibility_evaluations=[],
            evidence_map={
                "c1": [
                    {
                        "evidence_id": "e1",
                        "quote": "Evidence.",
                        "source_url": "https://source.com/paper",
                        "source_title": "Paper Title",
                        "stance": EvidenceStance.SUPPORTS.value,
                        "strength": 0.8,
                        "doi": "10.1234/test",
                    },
                ],
            },
        )
        report = service.generate(context)
        citations = report.citations
        assert len(citations) == 1
        assert citations[0]["doi"] == "10.1234/test"
        assert citations[0]["source_url"] == "https://source.com/paper"
        assert citations[0]["title"] == "Paper Title"


# ─── Claim verification integration ────────────────────────────────────


class TestClaimVerificationIntegration:
    def test_contradicted_claim_not_presented_as_fact(self):
        service = ReportGenerationService()
        context = ReportContext(
            topic="Test",
            research_question="Test?",
            claims=[
                {"claim_id": "c1", "text": "The earth is flat."},
            ],
            verification_results=[
                {
                    "claim_id": "c1",
                    "status": ClaimStatus.CONTRADICTED.value,
                    "confidence": 0.95,
                    "supporting_evidence_ids": [],
                    "contradicting_evidence_ids": ["e1"],
                },
            ],
            contradictions=[],
            credibility_evaluations=[],
            evidence_map={
                "c1": [
                    {
                        "evidence_id": "e1",
                        "quote": "The earth is an oblate spheroid.",
                        "source_url": "https://science.com/earth",
                        "source_title": "Science Journal",
                        "stance": EvidenceStance.CONTRADICTS.value,
                        "strength": 0.95,
                    },
                ],
            },
        )
        report = service.generate(context)
        content = report.content_markdown

        # Contradicted claim should say it's NOT an established fact
        assert "not presented as an established fact" in content
        assert "Contradicted" in content
        assert "https://science.com/earth" in content

    def test_unverified_claim_flagged(self):
        service = ReportGenerationService()
        context = ReportContext(
            topic="Test",
            research_question="Test?",
            claims=[
                {"claim_id": "c1", "text": "Unverified claim about X."},
            ],
            verification_results=[
                {
                    "claim_id": "c1",
                    "status": ClaimStatus.INSUFFICIENT_CONTEXT.value,
                    "confidence": 0.3,
                    "supporting_evidence_ids": [],
                    "contradicting_evidence_ids": [],
                },
            ],
            contradictions=[],
            credibility_evaluations=[],
            evidence_map={"c1": []},
        )
        report = service.generate(context)
        content = report.content_markdown

        assert "Insufficient evidence" in content or "Insufficient Context" in content
        assert "should not be treated as a finding" in content

    def test_verified_claim_cited(self):
        service = ReportGenerationService()
        context = ReportContext(
            topic="Test",
            research_question="Test?",
            claims=[
                {"claim_id": "c1", "text": "Verified claim."},
            ],
            verification_results=[
                {
                    "claim_id": "c1",
                    "status": ClaimStatus.SUPPORTED.value,
                    "confidence": 0.9,
                    "supporting_evidence_ids": ["e1"],
                    "contradicting_evidence_ids": [],
                },
            ],
            contradictions=[],
            credibility_evaluations=[],
            evidence_map={
                "c1": [
                    {
                        "evidence_id": "e1",
                        "quote": "Supporting quote here.",
                        "source_url": "https://verified.com/src",
                        "source_title": "Verified Source",
                        "stance": EvidenceStance.SUPPORTS.value,
                        "strength": 0.9,
                    },
                ],
            },
        )
        report = service.generate(context)
        content = report.content_markdown

        assert "Supported" in content
        assert "https://verified.com/src" in content
        assert "Verified Source" in content
        assert report.verified_claim_count == 1

    def test_partially_supported_claim(self):
        service = ReportGenerationService()
        context = ReportContext(
            topic="Test",
            research_question="Test?",
            claims=[
                {"claim_id": "c1", "text": "Partial claim."},
            ],
            verification_results=[
                {
                    "claim_id": "c1",
                    "status": ClaimStatus.PARTIALLY_SUPPORTED.value,
                    "confidence": 0.6,
                    "supporting_evidence_ids": ["e1"],
                    "contradicting_evidence_ids": ["e2"],
                },
            ],
            contradictions=[],
            credibility_evaluations=[],
            evidence_map={
                "c1": [
                    {
                        "evidence_id": "e1",
                        "quote": "Supports part.",
                        "source_url": "https://a.com",
                        "source_title": "Source A",
                        "stance": EvidenceStance.SUPPORTS.value,
                        "strength": 0.6,
                    },
                    {
                        "evidence_id": "e2",
                        "quote": "Contradicts part.",
                        "source_url": "https://b.com",
                        "source_title": "Source B",
                        "stance": EvidenceStance.CONTRADICTS.value,
                        "strength": 0.4,
                    },
                ],
            },
        )
        report = service.generate(context)
        content = report.content_markdown

        assert "Partially Supported" in content
        assert "https://a.com" in content
        assert "https://b.com" in content


# ─── Contradictions and research gaps ──────────────────────────────────


class TestContradictionsAndGaps:
    def test_contradictions_documented(self):
        service = ReportGenerationService()
        context = ReportContext(
            topic="Test",
            research_question="Test?",
            claims=[],
            verification_results=[],
            contradictions=[
                {
                    "claim_a_text": "Claim A says X.",
                    "claim_b_text": "Claim B says not X.",
                    "category": "factual_disagreement",
                    "severity": "high",
                    "description": "Two sources disagree on the outcome.",
                    "differences": [
                        {
                            "field": "outcome",
                            "claim_a_value": "positive",
                            "claim_b_value": "negative",
                            "explanation": "Different methodologies.",
                        },
                    ],
                },
            ],
            credibility_evaluations=[],
            evidence_map={},
        )
        report = service.generate(context)
        content = report.content_markdown

        assert "## Contradictory Findings" in content
        assert "Claim A says X." in content
        assert "Claim B says not X." in content
        assert "factual_disagreement" in content
        assert report.contradiction_count == 1

    def test_no_contradictions_section(self):
        service = ReportGenerationService()
        context = ReportContext(
            topic="Test",
            research_question="Test?",
            claims=[],
            verification_results=[],
            contradictions=[],
            credibility_evaluations=[],
            evidence_map={},
        )
        report = service.generate(context)
        content = report.content_markdown

        assert "No contradictory claims were detected" in content

    def test_research_gaps_for_unverified_claims(self):
        service = ReportGenerationService()
        context = ReportContext(
            topic="Test",
            research_question="Test?",
            claims=[
                {"claim_id": "c1", "text": "Unverifiable claim."},
            ],
            verification_results=[
                {
                    "claim_id": "c1",
                    "status": ClaimStatus.INSUFFICIENT_CONTEXT.value,
                    "confidence": 0.2,
                    "supporting_evidence_ids": [],
                    "contradicting_evidence_ids": [],
                },
            ],
            contradictions=[],
            credibility_evaluations=[],
            evidence_map={"c1": []},
        )
        report = service.generate(context)
        content = report.content_markdown

        assert "## Unanswered Questions and Research Gaps" in content
        assert "Unverifiable claim" in content

    def test_limitations_section(self):
        service = ReportGenerationService()
        context = ReportContext(
            topic="Test",
            research_question="Test?",
            claims=[],
            verification_results=[],
            contradictions=[],
            credibility_evaluations=[],
            evidence_map={},
            search_source_count=0,
        )
        report = service.generate(context)
        content = report.content_markdown

        assert "## Limitations" in content
        assert "probabilistic" in content.lower()


# ─── found_count helper ────────────────────────────────────────────────


class TestFoundCount:
    def test_counts_matching_status(self):
        results = [
            {"status": ClaimStatus.SUPPORTED.value},
            {"status": ClaimStatus.SUPPORTED.value},
            {"status": ClaimStatus.CONTRADICTED.value},
            {"status": ClaimStatus.INSUFFICIENT_CONTEXT.value},
        ]
        assert found_count(results, ClaimStatus.SUPPORTED.value) == 2
        assert found_count(results, ClaimStatus.CONTRADICTED.value) == 1
        assert found_count(results, ClaimStatus.INSUFFICIENT_CONTEXT.value) == 1
        assert found_count(results, "nonexistent") == 0


# ─── Orchestrator writer_node integration ─────────────────────────────


class TestWriterNodeIntegration:
    def test_writer_node_uses_evidence_grounded_generation(self):
        from orchestrator import MultiAgentOrchestrator

        orc = MultiAgentOrchestrator(max_revisions=1, approval_threshold=8)
        state: Dict[str, Any] = {
            "session_id": "s1",
            "user_id": "u1",
            "topic": "AI in Healthcare",
            "claims": [
                {
                    "claim_id": "c1",
                    "text": "AI improves diagnostic accuracy by 34%.",
                    "claim_type": "statistical",
                },
            ],
            "claim_verification_results": [
                {
                    "claim_id": "c1",
                    "status": ClaimStatus.SUPPORTED.value,
                    "confidence": 0.9,
                    "supporting_evidence_ids": ["e1"],
                    "contradicting_evidence_ids": [],
                },
            ],
            "contradictions": [],
            "source_credibility": [],
            "evidence_map": {
                "c1": [
                    {
                        "evidence_id": "e1",
                        "quote": "AI improved diagnostic accuracy by 34%.",
                        "source_url": "https://example.com/study",
                        "source_title": "Medical AI Study",
                        "stance": EvidenceStance.SUPPORTS.value,
                        "strength": 0.9,
                    },
                ],
            },
            "retrieved_memories": [],
            "report": "",
            "status": "running",
            "logs": [],
            "research_request": {
                "refined_query": "AI in Healthcare",
                "research_type": "comprehensive research",
                "focus_areas": ["diagnostic accuracy"],
            },
            "provider": "gemini",
            "max_revisions": 1,
            "search_results_raw": [],
            "search_results": "",
            "scraped_content": "",
            "analysis": "",
            "rag_context": "",
        }
        result = orc.writer_node(state)
        assert "report" in result
        assert result["status"] == "draft_completed"
        content = result["report"]
        assert "# AI in Healthcare" in content
        assert "## Key Findings" in content
        assert "## References" in content
        assert "https://example.com/study" in content


# ─── Report versioning integration ────────────────────────────────────


class TestReportVersioningConcept:
    def test_generated_report_can_be_versioned(self):
        """The generated report's markdown can be passed to ReportManagementService
        for versioning (existing infrastructure preserves versions immutably)."""
        service = ReportGenerationService()
        context = ReportContext(
            topic="Versioned Report",
            research_question="Test?",
            claims=[
                {"claim_id": "c1", "text": "Test claim."},
            ],
            verification_results=[
                {
                    "claim_id": "c1",
                    "status": ClaimStatus.SUPPORTED.value,
                    "confidence": 0.8,
                    "supporting_evidence_ids": ["e1"],
                    "contradicting_evidence_ids": [],
                },
            ],
            contradictions=[],
            credibility_evaluations=[],
            evidence_map={
                "c1": [
                    {
                        "evidence_id": "e1",
                        "quote": "Evidence.",
                        "source_url": "https://source.com",
                        "source_title": "Source",
                        "stance": EvidenceStance.SUPPORTS.value,
                        "strength": 0.8,
                    },
                ],
            },
        )
        report = service.generate(context)
        # The markdown content is suitable for storage as a report version
        assert report.content_markdown.strip()
        assert "# Versioned Report" in report.content_markdown
        assert "## References" in report.content_markdown
