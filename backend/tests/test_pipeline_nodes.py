"""Critic scoring, refiner behaviour and LangGraph state transitions."""

from __future__ import annotations

from typing import Any, Dict
from unittest.mock import MagicMock

import pytest

import orchestrator as orchestrator_module
from orchestrator import CriticEvaluation, MultiAgentOrchestrator, ResearchState


def _state(**overrides: Any) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "topic": "AI in healthcare",
        "provider": "gemini",
        "interactive": False,
        "max_revisions": 2,
        "approval_threshold": 8,
        "output_dir": "reports",
        "research_request": {
            "refined_query": "Clinical AI diagnostics",
            "focus_areas": ["Radiology", "Regulation"],
            "sub_queries": ["q1", "q2", "q3"],
        },
        "search_results_raw": [{"url": "https://example.com"}],
        "search_results": "results",
        "top_urls": ["https://example.com"],
        "source_summary": "one source",
        "scraped_content": "scraped body",
        "analysis": "analysis body",
        "report": "# Draft report\n\nBody.",
        "critic_score": 0,
        "critic_evaluation": None,
        "critic_feedback": "",
        "revision_count": 0,
        "revision_history": [],
        "final_report": "",
        "saved_report_path": "",
        "status": "started",
        "logs": [],
        "rag_context": "",
    }
    base.update(overrides)
    return base


@pytest.fixture
def orchestrator(monkeypatch) -> MultiAgentOrchestrator:
    monkeypatch.setattr(orchestrator_module, "console", MagicMock())
    monkeypatch.setattr(orchestrator_module, "get_llm", lambda **kwargs: MagicMock())
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


class TestCriticScoring:
    def test_structured_evaluation_is_returned(self, orchestrator) -> None:
        orchestrator.critic_chain = MagicMock()
        orchestrator.critic_chain.invoke.return_value = CriticEvaluation(
            score=9,
            passed=True,
            strengths=["Depth"],
            areas_for_improvement=["None"],
            actionable_revisions=["None"],
            verdict="Excellent",
        )
        result = orchestrator.critic_node(_state())
        assert result["critic_score"] == 9
        assert result["status"] == "critic_completed"
        assert result["critic_evaluation"]["score"] == 9
        assert "9/10" in result["critic_feedback"]

    def test_dict_output_is_coerced_into_the_model(self, orchestrator) -> None:
        orchestrator.critic_chain = MagicMock()
        orchestrator.critic_chain.invoke.return_value = {
            "score": 6,
            "passed": False,
            "strengths": ["Clear structure"],
            "areas_for_improvement": ["Thin evidence"],
            "actionable_revisions": ["Add studies"],
            "verdict": "Needs work",
        }
        result = orchestrator.critic_node(_state())
        assert result["critic_score"] == 6
        assert "NEEDS REVISION" in result["critic_feedback"]

    def test_malformed_output_falls_back_to_a_safe_evaluation(
        self, orchestrator
    ) -> None:
        orchestrator.critic_chain = MagicMock()
        orchestrator.critic_chain.invoke.side_effect = ValueError("invalid json")
        result = orchestrator.critic_node(_state())
        assert result["critic_score"] == 8
        assert result["critic_evaluation"]["passed"] is True
        assert result["status"] == "critic_completed"

    def test_score_is_bounded_by_the_model(self) -> None:
        with pytest.raises(Exception):
            CriticEvaluation(score=0, passed=False, verdict="bad")
        with pytest.raises(Exception):
            CriticEvaluation(score=11, passed=True, verdict="too good")


class TestRefinerBehaviour:
    def _critic_state(self, **overrides: Any) -> Dict[str, Any]:
        return _state(
            critic_score=6,
            critic_evaluation={
                "score": 6,
                "passed": False,
                "strengths": ["Clear structure"],
                "areas_for_improvement": ["Thin evidence"],
                "actionable_revisions": ["Add two peer-reviewed studies"],
                "verdict": "Needs work",
            },
            **overrides,
        )

    def test_refiner_increments_revision_and_records_history(self, orchestrator) -> None:
        orchestrator.refiner_chain = MagicMock()
        orchestrator.refiner_chain.invoke.return_value = "# Refined report"
        result = orchestrator.refiner_node(self._critic_state())
        assert result["report"] == "# Refined report"
        assert result["revision_count"] == 1
        assert result["status"] == "refined"
        entry = result["revision_history"][0]
        assert entry["cycle"] == 1
        assert entry["previous_score"] == 6
        assert entry["improvements_addressed"] == ["Add two peer-reviewed studies"]

    def test_refiner_passes_critic_feedback_to_the_chain(self, orchestrator) -> None:
        orchestrator.refiner_chain = MagicMock()
        orchestrator.refiner_chain.invoke.return_value = "# Refined"
        orchestrator.refiner_node(self._critic_state())
        prompt = orchestrator.refiner_chain.invoke.call_args.args[0]
        assert prompt["topic"] == "Clinical AI diagnostics"
        assert prompt["critic_score"] == 6
        assert "Add two peer-reviewed studies" in prompt["actionable_revisions"]
        assert "Thin evidence" in prompt["areas_for_improvement"]
        assert "ANALYST BRIEFING" in prompt["research"]

    def test_second_cycle_appends_to_existing_history(self, orchestrator) -> None:
        orchestrator.refiner_chain = MagicMock()
        orchestrator.refiner_chain.invoke.return_value = "# Refined again"
        prior = {"cycle": 1, "previous_score": 6, "improvements_addressed": []}
        result = orchestrator.refiner_node(
            self._critic_state(revision_count=1, revision_history=[prior])
        )
        assert result["revision_count"] == 2
        assert [entry["cycle"] for entry in result["revision_history"]] == [1, 2]


class TestStateTransitions:
    @pytest.mark.parametrize(
        "score,revisions,expected",
        [
            (9, 0, "exporter_node"),
            (8, 2, "exporter_node"),
            (7, 0, "refiner_node"),
            (0, 1, "refiner_node"),
            (5, 2, "exporter_node"),
        ],
    )
    def test_router_decisions(
        self, orchestrator, score: int, revisions: int, expected: str
    ) -> None:
        decision = orchestrator.evaluate_critic_decision(
            _state(critic_score=score, revision_count=revisions, max_revisions=2)
        )
        assert decision == expected

    def test_graph_contains_all_agent_nodes(self, orchestrator) -> None:
        nodes = set(orchestrator.graph.get_graph().nodes)
        for name in (
            "intake_node",
            "search_node",
            "reader_node",
            "rag_node",
            "analyst_node",
            "writer_node",
            "critic_node",
            "refiner_node",
            "exporter_node",
        ):
            assert name in nodes

    def test_graph_wires_the_self_correction_loop(self, orchestrator) -> None:
        graph = orchestrator.graph.get_graph()
        edges = set()
        for edge in graph.edges:
            if isinstance(edge, tuple):
                edges.add((edge[0], edge[1]))
            else:  # pragma: no cover - alternative graph API
                edges.add((edge.source, edge.target))
        for expected in (
            ("rag_node", "analyst_node"),
            ("writer_node", "critic_node"),
            ("critic_node", "refiner_node"),
            ("refiner_node", "critic_node"),
            ("critic_node", "exporter_node"),
        ):
            assert expected in edges, f"missing graph edge {expected}"

    def test_run_seeds_every_state_key(self, orchestrator) -> None:
        captured: Dict[str, Any] = {}

        def fake_invoke(state):
            captured.update(state)
            return state

        orchestrator.graph.invoke = fake_invoke  # type: ignore[assignment]
        result = orchestrator.run("AI in healthcare")

        missing = set(ResearchState.__annotations__) - set(captured)
        assert missing == set()
        assert result["status"] == "started"
        assert result["logs"] == ["Research pipeline initialized"]
        assert result["revision_count"] == 0


class TestNodeInstrumentation:
    def test_successful_stage_forwards_result_and_logs(self, caplog) -> None:
        import logging

        from app.orchestration.instrumentation import instrument_node

        wrapped = instrument_node("search", lambda state: {"status": "search_completed"})
        with caplog.at_level(logging.INFO, logger="research.agent"):
            result = wrapped({"topic": "x"})

        assert result == {"status": "search_completed"}
        messages = [record.message for record in caplog.records]
        assert "agent.stage.started" in messages
        assert "agent.stage.finished" in messages

    def test_failed_stage_logs_and_reraises(self, caplog) -> None:
        import logging

        from app.orchestration.instrumentation import instrument_node

        def boom(state):
            raise RuntimeError("stage exploded")

        wrapped = instrument_node("reader", boom)
        with caplog.at_level(logging.ERROR, logger="research.agent"):
            with pytest.raises(RuntimeError, match="stage exploded"):
                wrapped({})

        assert "agent.stage.failed" in [record.message for record in caplog.records]
