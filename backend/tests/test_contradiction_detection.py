"""Tests for Task 3.4 research contradiction detection."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

import orchestrator as orchestrator_module
from app.database.models import Base
from app.infrastructure.ports import ResearchExecutionRequest
from app.infrastructure.research_executor import LocalResearchExecutor
from app.schemas.claim import ContradictionAnalysis
from app.schemas.enums import ContradictionCategory, ContradictionStatus
from app.services.contradiction_detection import (
    ContradictionDetectionService,
    format_contradictions_appendix,
    format_contradictions_context,
)
from orchestrator import MultiAgentOrchestrator


BASE = "AI improves diagnostic accuracy in radiology"
SUBJECT = "AI diagnostic accuracy"


def _claim(
    claim_id: str,
    text: str = BASE,
    *,
    subject: str = SUBJECT,
    **overrides,
) -> dict:
    claim = {"claim_id": claim_id, "text": text, "subject": subject}
    claim.update(overrides)
    return claim


class TestAllCategories:
    @pytest.fixture
    def service(self) -> ContradictionDetectionService:
        return ContradictionDetectionService()

    def test_direct_contradiction(self, service):
        result = service.analyze_pair(
            _claim("a"),
            _claim("b", "AI does not improve diagnostic accuracy in radiology"),
        )
        assert result is not None
        assert result.category is ContradictionCategory.DIRECT_CONTRADICTION
        assert result.severity.value == "high"

    def test_partial_contradiction(self, service):
        result = service.analyze_pair(
            _claim("a"),
            _claim(
                "b",
                "AI is ineffective for pathology workflows, unlike radiology",
            ),
        )
        assert result is not None
        assert result.category is ContradictionCategory.PARTIAL_CONTRADICTION

    def test_contextual_difference(self, service):
        result = service.analyze_pair(
            _claim("a", population="adults"),
            _claim(
                "b",
                "AI does not improve diagnostic accuracy in radiology",
                population="children",
            ),
        )
        assert result is not None
        assert result.category is ContradictionCategory.CONTEXTUAL_DIFFERENCE
        assert any(d.field == "population" for d in result.differences)

    def test_methodological_difference(self, service):
        result = service.analyze_pair(
            _claim("a", methodology="randomized controlled trial"),
            _claim(
                "b",
                "AI does not improve diagnostic accuracy in radiology",
                methodology="retrospective cohort",
            ),
        )
        assert result is not None
        assert result.category is ContradictionCategory.METHODOLOGICAL_DIFFERENCE
        assert any(d.field == "methodology" for d in result.differences)

    def test_temporal_difference(self, service):
        result = service.analyze_pair(
            _claim("a", time_period="2015"),
            _claim(
                "b",
                "AI does not improve diagnostic accuracy in radiology",
                time_period="2024",
            ),
        )
        assert result is not None
        assert result.category is ContradictionCategory.TEMPORAL_DIFFERENCE
        assert any(d.field == "time_period" for d in result.differences)

    def test_no_contradiction(self, service):
        result = service.analyze_pair(
            _claim("a"),
            _claim("b", "AI improves diagnostic accuracy in radiology"),
        )
        assert result is not None
        assert result.category is ContradictionCategory.NO_CONTRADICTION
        assert result.requires_human_review is False

    def test_insufficient_information(self, service):
        result = service.analyze_pair(
            _claim("a", "AI is used in radiology", subject="AI in radiology"),
            _claim("b", "AI is used in pathology", subject="AI in radiology"),
        )
        assert result is not None
        assert result.category is ContradictionCategory.INSUFFICIENT_INFORMATION
        assert result.status is ContradictionStatus.UNRESOLVED

    def test_all_categories_are_exposed(self):
        assert {category.value for category in ContradictionCategory} == {
            "direct_contradiction",
            "partial_contradiction",
            "contextual_difference",
            "methodological_difference",
            "temporal_difference",
            "no_contradiction",
            "insufficient_information",
        }


class TestEvidenceAndScope:
    def test_both_sides_keep_their_evidence(self):
        a_evidence = str(uuid.uuid4())
        b_evidence = str(uuid.uuid4())
        result = ContradictionDetectionService().analyze_pair(
            _claim(
                "a",
                supporting_evidence=[{"evidence_id": a_evidence}],
            ),
            _claim(
                "b",
                "AI does not improve diagnostic accuracy in radiology",
                contradicting_evidence=[{"evidence_id": b_evidence}],
            ),
        )
        assert result is not None
        assert result.claim_a_evidence_ids == [a_evidence]
        assert result.claim_b_evidence_ids == [b_evidence]

    def test_unrelated_claims_are_not_compared(self):
        result = ContradictionDetectionService().analyze_pair(
            _claim("a"),
            _claim("b", "Medieval cookery uses open fire", subject="Medieval cookery"),
        )
        assert result is None

    def test_no_winner_is_encoded(self):
        result = ContradictionDetectionService().analyze_pair(
            _claim("a", time_period="2015"),
            _claim(
                "b",
                "AI does not improve diagnostic accuracy in radiology",
                time_period="2024",
            ),
        )
        assert result is not None
        assert result.status is ContradictionStatus.UNRESOLVED
        assert result.requires_human_review is True
        assert "neither claim is automatically preferred" in result.description

    def test_report_formatting_preserves_both_sides(self):
        result = ContradictionDetectionService().analyze_pair(
            _claim("a"),
            _claim("b", "AI does not improve diagnostic accuracy in radiology"),
        )
        assert result is not None
        context = format_contradictions_context([result])
        appendix = format_contradictions_appendix([result])
        assert "preserve both claims" in context
        assert "AI improves diagnostic accuracy" in context
        assert "AI does not improve diagnostic accuracy" in context
        assert "no source is discarded" in appendix
        assert "unresolved" in appendix


class TestDatabaseMetadata:
    def test_contradiction_has_category_and_analysis_context(self):
        table = Base.metadata.tables["research_contradictions"]
        assert {"category", "analysis_json"}.issubset(table.columns.keys())

    def test_contradiction_evidence_links_both_side_evidence(self):
        table = Base.metadata.tables["contradiction_evidence"]
        targets = {fk.target_fullname for fk in table.foreign_keys}
        assert "research_contradictions.id" in targets
        assert "research_evidence.id" in targets
        assert {"contradiction_id", "evidence_id", "side"}.issubset(
            table.columns.keys()
        )


class TestWorkflowAndPersistence:
    @pytest.fixture
    def orchestrator(self, monkeypatch) -> MultiAgentOrchestrator:
        monkeypatch.setattr(orchestrator_module, "console", MagicMock())
        monkeypatch.setattr(
            orchestrator_module, "get_llm", lambda **kwargs: MagicMock()
        )
        for builder in (
            "build_reader_agent",
            "build_analyst_chain",
            "build_writer_chain",
            "build_critic_chain",
            "build_refiner_chain",
        ):
            monkeypatch.setattr(
                orchestrator_module, builder, lambda **kwargs: MagicMock()
            )
        return MultiAgentOrchestrator(
            provider="gemini", max_revisions=2, approval_threshold=8
        )

    def test_analyst_node_adds_contradictions(self, orchestrator):
        orchestrator.analyst_chain = MagicMock()
        orchestrator.analyst_chain.invoke.return_value = "briefing"
        state = {
            "topic": "Clinical AI",
            "research_request": {"refined_query": "Clinical AI"},
            "logs": [],
            "claims": [
                _claim("a"),
                _claim("b", "AI does not improve diagnostic accuracy in radiology"),
            ],
        }
        result = orchestrator.analyst_node(state)  # type: ignore[arg-type]
        assert len(result["contradictions"]) == 1
        assert result["contradictions"][0]["category"] == "direct_contradiction"

    def test_claims_node_extracts_and_detects(self, orchestrator):
        state = {
            "topic": "AI diagnostic accuracy",
            "research_request": {
                "refined_query": "AI diagnostic accuracy",
            },
            "analysis": (
                "AI improves diagnostic accuracy in radiology. "
                "However, AI does not improve diagnostic accuracy in radiology."
            ),
            "logs": [],
            "search_results_raw": [
                {
                    "title": "Radiology study",
                    "url": "https://example.com/radiology",
                    "snippet": "AI improves diagnostic accuracy in radiology",
                    "source_name": "Example",
                }
            ],
        }
        result = orchestrator.claims_node(state)  # type: ignore[arg-type]
        assert len(result["claims"]) >= 2
        assert all(claim["evidence_ids"] for claim in result["claims"])
        assert any(
            item["category"] == "direct_contradiction"
            for item in result["contradictions"]
        )
        assert result["contradictions"][0]["claim_a_evidence_ids"]

    def test_writer_receives_contradiction_context(self, orchestrator):
        orchestrator.writer_chain = MagicMock()
        orchestrator.writer_chain.invoke.return_value = "# Report"
        state = {
            "topic": "Clinical AI",
            "research_request": {
                "refined_query": "Clinical AI",
                "research_type": "exploratory",
                "focus_areas": ["Diagnostics"],
            },
            "search_results": "search",
            "scraped_content": "scraped",
            "analysis": "analysis",
            "source_summary": "summary",
            "contradictions": [
                ContradictionAnalysis(
                    claim_a_id="a",
                    claim_b_id="b",
                    claim_a_text=BASE,
                    claim_b_text="AI does not improve diagnostic accuracy in radiology",
                    category=ContradictionCategory.DIRECT_CONTRADICTION,
                    description="Both are preserved.",
                ).model_dump(mode="json")
            ],
        }
        result = orchestrator.writer_node(state)  # type: ignore[arg-type]
        prompt = orchestrator.writer_chain.invoke.call_args.args[0]
        assert "CONTRADICTION ANALYSIS" in prompt["research"]
        assert BASE in prompt["research"]
        assert result["status"] == "draft_completed"

    def test_executor_materializes_claims_before_contradictions(self, monkeypatch):
        calls = []

        class FakeClaimRepository:
            def save_claims(self, **kwargs):
                calls.append("claims")

            def save_evidence(self, **kwargs):
                calls.append("evidence")

            def save_claim_evidence_links(self, **kwargs):
                calls.append("links")

            def save_verification_results(self, **kwargs):
                calls.append("results")

        class FakeContradictionRepository:
            def save_analyses(self, **kwargs):
                calls.append("contradictions")

        class FakeOrchestrator:
            def run(self, topic):
                evidence_id = str(uuid.uuid4())
                return {
                    "status": "completed",
                    "session_id": "session-1",
                    "claims": [
                        {
                            "claim_id": str(uuid.uuid4()),
                            "text": BASE,
                            "supporting_evidence": [
                                {
                                    "evidence_id": evidence_id,
                                    "quote": BASE,
                                    "stance": "supports",
                                    "strength": 1.0,
                                }
                            ],
                        }
                    ],
                    "claim_verification_results": [
                        {"claim_id": "a", "status": "unverified"}
                    ],
                    "contradictions": [
                        {"claim_a_id": "a", "claim_b_id": "b"}
                    ],
                }

        monkeypatch.setattr(
            "app.orchestration.pipeline.build_orchestrator",
            lambda **kwargs: FakeOrchestrator(),
        )
        LocalResearchExecutor(
            claim_repository=FakeClaimRepository(),
            contradiction_repository=FakeContradictionRepository(),
        ).execute(
            ResearchExecutionRequest(
                topic="Clinical AI", provider="groq", research_id="run-1"
            )
        )
        assert calls == ["claims", "evidence", "links", "results", "contradictions"]

    def test_executor_persists_analyses_best_effort(self, monkeypatch):
        stored = {}

        class FakeRepository:
            def save_analyses(self, *, session_id, analyses):
                stored["session_id"] = session_id
                stored["analyses"] = analyses

        class FakeOrchestrator:
            def run(self, topic):
                return {
                    "status": "completed",
                    "session_id": "session-1",
                    "contradictions": [
                        {
                            "claim_a_id": "a",
                            "claim_b_id": "b",
                            "category": "direct_contradiction",
                        }
                    ],
                }

        monkeypatch.setattr(
            "app.orchestration.pipeline.build_orchestrator",
            lambda **kwargs: FakeOrchestrator(),
        )
        LocalResearchExecutor(
            contradiction_repository=FakeRepository()
        ).execute(
            ResearchExecutionRequest(
                topic="Clinical AI", provider="groq", research_id="run-1"
            )
        )
        assert stored["session_id"] == "session-1"
        assert stored["analyses"][0]["category"] == "direct_contradiction"
