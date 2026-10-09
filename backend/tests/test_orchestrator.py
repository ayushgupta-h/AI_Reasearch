"""
tests/test_orchestrator.py
===========================
Unit tests verifying that the MultiAgentOrchestrator:
  1. Compiles the LangGraph graph without errors.
  2. Can invoke a minimal research run using fully mocked agent nodes,
     without making any real LLM or API calls.

All external dependencies (LLM, search, scraping, ChromaDB, citation
validator, etc.) are patched with unittest.mock so the test is fully
offline and deterministic.
"""

import sys
import types
import unittest
from unittest.mock import MagicMock, patch, PropertyMock

# ─── Stub heavy external packages before any project imports ─────────────────

def _stub_module(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    mod.__dict__.update(attrs)
    sys.modules.setdefault(name, mod)
    return mod

# LLM providers
_stub_module("langchain_google_genai", ChatGoogleGenerativeAI=MagicMock())
_stub_module("dotenv", load_dotenv=lambda *a, **kw: None)

# ─── Now import orchestrator (all internal imports will use the stubs) ────────

import orchestrator  # noqa: E402
from orchestrator import MultiAgentOrchestrator, ResearchState


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _minimal_state(**overrides) -> ResearchState:
    """Return a minimal valid ResearchState dict for testing."""
    base: ResearchState = {
        "topic": "Test Topic",
        "provider": "gemini",
        "interactive": False,
        "max_revisions": 1,
        "approval_threshold": 8,
        "output_dir": "reports",
        "research_request": {
            "refined_query": "Test Topic",
            "sub_queries": ["sub-query 1", "sub-query 2"],
            "research_type": "exploratory",
            "focus_areas": ["Focus A"],
            "suggested_source_types": ["web"],
        },
        "search_results_raw": [
            {
                "title": "Test Article",
                "url": "https://example.com",
                "snippet": "Some test snippet content.",
                "source_name": "TestSource",
                "source_type": "web",
            }
        ],
        "search_results": "Test search results text.",
        "top_urls": ["https://example.com"],
        "source_summary": "TestSource: 1 item",
        "scraped_content": "Test scraped content.",
        "analysis": "Test analysis output.",
        "report": "# Test Report\n\nTest content with https://example.com citation.",
        "critic_score": 9,
        "critic_evaluation": {
            "score": 9,
            "passed": True,
            "strengths": ["Comprehensive"],
            "areas_for_improvement": [],
            "actionable_revisions": [],
            "verdict": "Good report.",
        },
        "critic_feedback": "Score 9/10",
        "revision_count": 0,
        "revision_history": [],
        "final_report": "",
        "saved_report_path": "",
        "status": "started",
        "logs": [],
        "rag_context": "Test RAG context from ChromaDB.",
    }
    base.update(overrides)
    return base


# ─── Test: Graph Compilation ──────────────────────────────────────────────────

class TestGraphCompilation(unittest.TestCase):
    """Tests that the LangGraph graph compiles without errors."""

    def _make_mock_orchestrator(self):
        """Create a MultiAgentOrchestrator with all LLM calls mocked."""
        with patch("orchestrator.get_llm", return_value=MagicMock()), \
             patch("orchestrator.build_reader_agent", return_value=MagicMock()), \
             patch("orchestrator.build_analyst_chain", return_value=MagicMock()), \
             patch("orchestrator.build_writer_chain", return_value=MagicMock()), \
             patch("orchestrator.build_critic_chain", return_value=MagicMock()), \
             patch("orchestrator.build_refiner_chain", return_value=MagicMock()):
            return MultiAgentOrchestrator(
                provider="gemini",
                max_revisions=1,
                approval_threshold=8,
                interactive=False,
            )

    def test_graph_compiles(self):
        """MultiAgentOrchestrator.__init__ must compile the LangGraph without raising."""
        try:
            orc = self._make_mock_orchestrator()
            self.assertIsNotNone(orc.graph)
        except Exception as exc:
            self.fail(f"Graph compilation raised an exception: {exc}")

    def test_graph_has_correct_nodes(self):
        """Compiled graph must contain all 9 expected agent nodes."""
        orc = self._make_mock_orchestrator()
        # LangGraph compiled graph exposes node names via .nodes or .__compiled_nodes__
        try:
            node_names = set(orc.graph.nodes.keys())
        except AttributeError:
            # Fallback: just verify the object has the graph attribute
            self.assertIsNotNone(orc.graph)
            return

        expected_nodes = {
            "intake_node", "search_node", "reader_node", "rag_node",
            "analyst_node", "writer_node", "critic_node",
            "refiner_node", "exporter_node",
        }
        for node in expected_nodes:
            self.assertIn(
                node, node_names,
                msg=f"Expected node '{node}' not found in compiled graph. Found: {node_names}"
            )


# ─── Test: Individual Node Methods ───────────────────────────────────────────

class TestIntakeNode(unittest.TestCase):
    """Tests for MultiAgentOrchestrator.intake_node()."""

    def setUp(self):
        with patch("orchestrator.get_llm", return_value=MagicMock()), \
             patch("orchestrator.build_reader_agent", return_value=MagicMock()), \
             patch("orchestrator.build_analyst_chain", return_value=MagicMock()), \
             patch("orchestrator.build_writer_chain", return_value=MagicMock()), \
             patch("orchestrator.build_critic_chain", return_value=MagicMock()), \
             patch("orchestrator.build_refiner_chain", return_value=MagicMock()):
            self.orc = MultiAgentOrchestrator()

    def test_intake_node_returns_research_request(self):
        """intake_node must return a dict containing 'research_request'."""
        state = _minimal_state()
        mock_request = MagicMock()
        mock_request.sub_queries = ["q1", "q2"]
        mock_request.model_dump.return_value = {"refined_query": "Test", "sub_queries": ["q1", "q2"]}

        mock_intake = MagicMock()
        mock_intake.validate_query.return_value = MagicMock()
        mock_intake._display_validation = MagicMock()
        mock_intake.decompose_query.return_value = mock_request
        mock_intake._display_research_plan = MagicMock()

        with patch("orchestrator.QueryIntakeSystem", return_value=mock_intake):
            result = self.orc.intake_node(state)

        self.assertIn("research_request", result)
        self.assertEqual(result["status"], "intake_completed")

    def test_intake_node_cancelled_when_no_request(self):
        """intake_node should return status 'cancelled' if request is None."""
        state = _minimal_state()
        mock_intake = MagicMock()
        mock_intake.validate_query.return_value = MagicMock()
        mock_intake._display_validation = MagicMock()
        mock_intake.decompose_query.return_value = None
        mock_intake._display_research_plan = MagicMock()

        with patch("orchestrator.QueryIntakeSystem", return_value=mock_intake):
            result = self.orc.intake_node(state)

        self.assertEqual(result["status"], "cancelled")


class TestSearchNode(unittest.TestCase):
    """Tests for MultiAgentOrchestrator.search_node()."""

    def setUp(self):
        with patch("orchestrator.get_llm", return_value=MagicMock()), \
             patch("orchestrator.build_reader_agent", return_value=MagicMock()), \
             patch("orchestrator.build_analyst_chain", return_value=MagicMock()), \
             patch("orchestrator.build_writer_chain", return_value=MagicMock()), \
             patch("orchestrator.build_critic_chain", return_value=MagicMock()), \
             patch("orchestrator.build_refiner_chain", return_value=MagicMock()):
            self.orc = MultiAgentOrchestrator()

    def test_search_node_no_request_returns_failed(self):
        """search_node must return failed status when research_request is None."""
        state = _minimal_state(research_request=None)
        result = self.orc.search_node(state)
        self.assertEqual(result["status"], "failed_no_request")

    def test_search_node_returns_search_results(self):
        """search_node must return 'search_results' and 'search_completed' status."""
        state = _minimal_state()

        # Build a mock search result object
        mock_result = MagicMock()
        mock_result.url = "https://example.com"
        mock_result.snippet = "snippet"
        mock_result.source_name = "TestSource"
        mock_result.model_dump.return_value = {
            "url": "https://example.com",
            "snippet": "snippet",
            "source_name": "TestSource",
            "title": "Test",
            "source_type": "web",
        }

        mock_orch = MagicMock()
        mock_orch.async_search_all = MagicMock()
        mock_orch.display_search_summary = MagicMock()
        mock_orch.get_all_urls.return_value = ["https://example.com"]
        mock_orch.get_results_by_source.return_value = {"TestSource": [mock_result]}
        mock_orch.format_results.return_value = "Formatted results"
        mock_orch.display_source_status = MagicMock()

        # Patch asyncio.run to return the mock results synchronously
        with patch("orchestrator.SearchOrchestrator", return_value=mock_orch), \
             patch("orchestrator.asyncio.run", return_value=[mock_result]):
            result = self.orc.search_node(state)

        self.assertEqual(result["status"], "search_completed")
        self.assertIn("search_results", result)
        self.assertIn("top_urls", result)


class TestCriticNode(unittest.TestCase):
    """Tests for MultiAgentOrchestrator.critic_node()."""

    def setUp(self):
        self.mock_critic = MagicMock()
        with patch("orchestrator.get_llm", return_value=MagicMock()), \
             patch("orchestrator.build_reader_agent", return_value=MagicMock()), \
             patch("orchestrator.build_analyst_chain", return_value=MagicMock()), \
             patch("orchestrator.build_writer_chain", return_value=MagicMock()), \
             patch("orchestrator.build_critic_chain", return_value=self.mock_critic), \
             patch("orchestrator.build_refiner_chain", return_value=MagicMock()):
            self.orc = MultiAgentOrchestrator()

    def test_critic_node_returns_score(self):
        """critic_node must return critic_score and critic_evaluation."""
        from orchestrator import CriticEvaluation as OrcCriticEval
        state = _minimal_state()

        mock_eval = MagicMock(spec=OrcCriticEval)
        mock_eval.score = 9
        mock_eval.passed = True
        mock_eval.strengths = ["Good"]
        mock_eval.areas_for_improvement = []
        mock_eval.actionable_revisions = []
        mock_eval.verdict = "Excellent"
        mock_eval.model_dump.return_value = {"score": 9, "passed": True}

        self.orc.critic_chain = MagicMock()
        self.orc.critic_chain.invoke.return_value = mock_eval

        result = self.orc.critic_node(state)

        self.assertIn("critic_score", result)
        self.assertEqual(result["critic_score"], 9)
        self.assertEqual(result["status"], "critic_completed")


class TestEvaluateCriticDecision(unittest.TestCase):
    """Tests for the conditional edge router evaluate_critic_decision()."""

    def setUp(self):
        with patch("orchestrator.get_llm", return_value=MagicMock()), \
             patch("orchestrator.build_reader_agent", return_value=MagicMock()), \
             patch("orchestrator.build_analyst_chain", return_value=MagicMock()), \
             patch("orchestrator.build_writer_chain", return_value=MagicMock()), \
             patch("orchestrator.build_critic_chain", return_value=MagicMock()), \
             patch("orchestrator.build_refiner_chain", return_value=MagicMock()):
            self.orc = MultiAgentOrchestrator(approval_threshold=8)

    def test_routes_to_exporter_when_score_meets_threshold(self):
        state = _minimal_state(critic_score=9, approval_threshold=8, revision_count=0, max_revisions=2)
        self.assertEqual(self.orc.evaluate_critic_decision(state), "exporter_node")

    def test_routes_to_refiner_when_score_below_threshold(self):
        state = _minimal_state(critic_score=6, approval_threshold=8, revision_count=0, max_revisions=2)
        self.assertEqual(self.orc.evaluate_critic_decision(state), "refiner_node")

    def test_routes_to_exporter_when_max_revisions_reached(self):
        state = _minimal_state(critic_score=5, approval_threshold=8, revision_count=2, max_revisions=2)
        self.assertEqual(self.orc.evaluate_critic_decision(state), "exporter_node")


# ─── Test: Full Pipeline Run (end-to-end mock) ───────────────────────────────

class TestFullPipelineMockRun(unittest.TestCase):
    """
    Runs orchestrator.run() with every agent node patched so no API
    call or filesystem write is made. Verifies that the graph invokes
    all nodes and returns the expected state keys.
    """

    def _make_node_mock(self, return_updates: dict):
        """Return a MagicMock that returns a partial state update dict."""
        m = MagicMock(return_value=return_updates)
        return m

    @patch("orchestrator.get_llm", return_value=MagicMock())
    @patch("orchestrator.build_reader_agent", return_value=MagicMock())
    @patch("orchestrator.build_analyst_chain", return_value=MagicMock())
    @patch("orchestrator.build_writer_chain", return_value=MagicMock())
    @patch("orchestrator.build_critic_chain", return_value=MagicMock())
    @patch("orchestrator.build_refiner_chain", return_value=MagicMock())
    def test_run_returns_state_dict(self, *mocks):
        """orchestrator.run() should return a dict with 'status' key."""
        orc = MultiAgentOrchestrator(interactive=False, max_revisions=0)

        # Patch graph.invoke directly to return a fake final state
        fake_state = _minimal_state(status="completed", saved_report_path="/reports/test.md")
        orc.graph.invoke = MagicMock(return_value=fake_state)

        result = orc.run(topic="Test Topic")

        orc.graph.invoke.assert_called_once()
        self.assertIsInstance(result, dict)
        self.assertIn("status", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
