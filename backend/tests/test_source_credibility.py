"""Tests for Task 3.3 source credibility evaluation."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest
import orchestrator as orchestrator_module
from app.database.models import Base
from app.schemas.source_credibility import (
    CredibilityFactorStatus,
    CredibilityLabel,
    SourceCredibilityEvaluation,
)
from app.infrastructure.ports import ResearchExecutionRequest
from app.infrastructure.research_executor import LocalResearchExecutor
from app.services.source_credibility import (
    CredibilityConfig,
    SourceCredibilityService,
    format_credibility_appendix,
    format_credibility_context,
)
from orchestrator import MultiAgentOrchestrator
from search_sources import SearchResult


def _complete_source(**overrides):
    today = date.today()
    source = {
        "title": "Clinical AI diagnostics: a cohort study",
        "url": "https://www.nature.com/articles/example",
        "publisher": "Nature",
        "authors": ["Ada Lovelace", "Grace Hopper"],
        "publication_date": (today - timedelta(days=30)).isoformat(),
        "doi": "10.1038/example",
        "venue": "Nature",
        "methodology": "Prospective cohort study with external validation.",
        "peer_reviewed": True,
        "references_available": True,
        "source_nature": "primary",
        "source_type": "academic",
    }
    source.update(overrides)
    return source


class TestExplainableEvaluation:
    def test_complete_metadata_produces_high_confidence_explanation(self):
        evaluation = SourceCredibilityService().evaluate(
            _complete_source(), query="clinical AI diagnostics"
        )

        assert evaluation.score is not None
        assert evaluation.confidence >= 0.8
        assert evaluation.label is CredibilityLabel.HIGH
        assert evaluation.missing_fields == []
        assert all(
            factor.reason for factor in evaluation.factors
        ), "every factor must explain itself"
        assert any(
            factor.status is CredibilityFactorStatus.POSITIVE
            for factor in evaluation.factors
        )

    def test_missing_metadata_is_missing_not_negative(self):
        evaluation = SourceCredibilityService().evaluate(
            {
                "title": "An unlabeled web page",
                "url": "https://example.org/page",
            },
            query="unrelated query",
        )

        assert evaluation.score is None
        assert evaluation.confidence < 0.30
        assert evaluation.label is CredibilityLabel.INSUFFICIENT_INFORMATION
        assert evaluation.missing_fields
        assert all(
            factor.status is not CredibilityFactorStatus.NEGATIVE
            for factor in evaluation.factors
        )

    def test_old_publication_is_not_automatically_unreliable(self):
        old = _complete_source(publication_date="2008-01-01")
        recent = _complete_source()
        service = SourceCredibilityService()

        old_evaluation = service.evaluate(old, query="clinical AI diagnostics")
        recent_evaluation = service.evaluate(
            recent, query="clinical AI diagnostics"
        )

        assert old_evaluation.label is CredibilityLabel.HIGH
        assert old_evaluation.score is not None
        recency = next(
            f for f in old_evaluation.factors
            if f.factor == "publication_recency"
        )
        assert recency.status is CredibilityFactorStatus.NEUTRAL
        assert "not automatically" in recency.reason

    def test_academic_looking_unknown_site_is_not_treated_as_credible(self):
        evaluation = SourceCredibilityService().evaluate(
            {
                "title": "A paper",
                "url": "https://fake-university.edu/paper",
                "source_type": "academic",
            },
            query="quantum computing",
        )

        assert evaluation.score is None
        publisher = next(
            f for f in evaluation.factors
            if f.factor == "publisher_reputation"
        )
        assert publisher.status is CredibilityFactorStatus.MISSING
        assert evaluation.label is CredibilityLabel.INSUFFICIENT_INFORMATION

    def test_peer_review_is_not_assumed_when_unknown(self):
        evaluation = SourceCredibilityService().evaluate(
            _complete_source(peer_reviewed=None),
            query="clinical AI diagnostics",
        )
        peer = next(
            f for f in evaluation.factors if f.factor == "peer_review_status"
        )
        assert peer.status is CredibilityFactorStatus.MISSING
        assert "not independently verified" in peer.reason

    def test_explicit_missing_references_is_a_negative_signal(self):
        missing = SourceCredibilityService().evaluate(
            _complete_source(references_available=None),
            query="clinical AI diagnostics",
        )
        negative = SourceCredibilityService().evaluate(
            _complete_source(references_available=False),
            query="clinical AI diagnostics",
        )

        missing_factor = next(
            f for f in missing.factors if f.factor == "supporting_references"
        )
        negative_factor = next(
            f for f in negative.factors if f.factor == "supporting_references"
        )
        assert missing_factor.status is CredibilityFactorStatus.MISSING
        assert negative_factor.status is CredibilityFactorStatus.NEGATIVE
        assert "not proof of unreliability" in negative_factor.reason

    def test_configuration_can_change_weights(self):
        config = CredibilityConfig(
            publisher_weight=0,
            author_weight=0.5,
            recency_weight=0,
            source_nature_weight=0,
            methodology_weight=0,
            references_weight=0,
            peer_review_weight=0,
            relevance_weight=0,
            minimum_confidence=0.1,
        )
        evaluation = SourceCredibilityService(config=config).evaluate(
            _complete_source(), query="clinical AI diagnostics"
        )

        assert evaluation.score == 0.85
        assert evaluation.confidence == 1.0
        assert evaluation.label is CredibilityLabel.HIGH

    def test_context_and_appendix_keep_the_caveat_visible(self):
        evaluation = SourceCredibilityService().evaluate(
            {
                "title": "Unknown source",
                "url": "https://example.org/unknown",
            },
            query="anything",
        )
        context = format_credibility_context([evaluation])
        appendix = format_credibility_appendix([evaluation])

        assert "not a verification of truth" in context
        assert "insufficient information" in context
        assert "not an objective" in appendix or "not objective" in appendix
        assert "Unknown source" in appendix


class TestCredibilitySchemasAndPersistence:
    def test_schema_rejects_an_out_of_range_score(self):
        with pytest.raises(Exception):
            SourceCredibilityEvaluation(
                score=1.5,
                confidence=1,
                label=CredibilityLabel.HIGH,
                factors=[],
            )

    def test_postgres_table_links_to_existing_source_records(self):
        table = Base.metadata.tables["source_credibility_evaluations"]
        foreign_keys = {fk.target_fullname for fk in table.foreign_keys}
        assert "research_sources.id" in foreign_keys
        columns = set(table.columns.keys())
        assert {
            "score",
            "confidence",
            "label",
            "factors_json",
            "missing_fields_json",
            "metadata_json",
        }.issubset(columns)

    def test_table_is_registered_for_alembic(self):
        inspector_names = set(Base.metadata.tables)
        assert "source_credibility_evaluations" in inspector_names


class TestPersistenceHook:
    def test_executor_persists_evaluations_after_a_successful_run(
        self, monkeypatch
    ):
        stored: dict = {}

        class FakeRepository:
            def save_evaluations(self, *, research_id, evaluations):
                stored["research_id"] = research_id
                stored["evaluations"] = evaluations

        class FakeOrchestrator:
            def run(self, topic):
                return {
                    "status": "completed",
                    "source_credibility": [
                        {
                            "title": "Paper",
                            "source_url": "https://example.org/paper",
                            "score": None,
                            "confidence": 0.0,
                            "label": "insufficient_information",
                        }
                    ],
                }

        monkeypatch.setattr(
            "app.orchestration.pipeline.build_orchestrator",
            lambda **kwargs: FakeOrchestrator(),
        )
        result = LocalResearchExecutor(
            credibility_repository=FakeRepository()
        ).execute(
            ResearchExecutionRequest(
                topic="Clinical AI",
                provider="groq",
                research_id="run-credibility",
            )
        )

        assert result["status"] == "completed"
        assert stored["research_id"] == "run-credibility"
        assert stored["evaluations"][0]["source_url"] == "https://example.org/paper"


class TestPipelineIntegration:
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

    def test_search_attaches_evaluations_without_rejecting_sources(
        self, orchestrator, monkeypatch
    ):
        sources = [
            SearchResult(
                title="Clinical AI diagnostics",
                url="https://www.nature.com/articles/example",
                snippet="A cohort study.",
                source_name="Tavily",
                source_type="academic",
                publisher="Nature",
                authors=["Ada Lovelace"],
                publication_date=date.today().isoformat(),
                methodology="Cohort study.",
                peer_reviewed=True,
                references_available=True,
                source_nature="primary",
            ),
            SearchResult(
                title="Unclear source",
                url="https://example.org/unclear",
                snippet="Some text.",
                source_name="DuckDuckGo",
                source_type="web",
            ),
        ]

        class FakeSearchOrchestrator:
            def display_source_status(self):
                return None

            async def async_search_all(self, **kwargs):
                return sources

            def display_search_summary(self, results):
                return None

            @staticmethod
            def get_all_urls(results):
                return [r.url for r in results]

            @staticmethod
            def get_results_by_source(results):
                return {"test": results}

            @staticmethod
            def format_results(results):
                return "formatted"

        monkeypatch.setattr(
            orchestrator_module, "SearchOrchestrator", FakeSearchOrchestrator
        )
        state = {
            "topic": "Clinical AI diagnostics",
            "research_request": {
                "refined_query": "Clinical AI diagnostics",
                "sub_queries": ["clinical AI"],
                "suggested_source_types": ["academic", "web"],
            },
            "logs": [],
        }

        result = orchestrator.search_node(state)  # type: ignore[arg-type]

        assert len(result["search_results_raw"]) == 2
        assert len(result["source_credibility"]) == 2
        assert result["search_results_raw"][0]["credibility"]["score"] is not None
        assert result["search_results_raw"][1]["credibility"]["score"] is None

    def test_writer_receives_credibility_context(self, orchestrator):
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
            "source_summary": "source summary",
            "source_credibility": [
                {
                    "title": "Paper",
                    "source_url": "https://example.org/paper",
                    "score": None,
                    "confidence": 0.0,
                    "label": "insufficient_information",
                    "factors": [],
                    "missing_fields": ["author_information"],
                }
            ],
        }

        result = orchestrator.writer_node(state)  # type: ignore[arg-type]

        prompt = orchestrator.writer_chain.invoke.call_args.args[0]
        assert "SOURCE CREDIBILITY" in prompt["research"]
        assert "not a verification of truth" in prompt["research"]
        assert result["status"] == "draft_completed"
