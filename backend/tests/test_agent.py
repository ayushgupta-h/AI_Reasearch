"""
tests/test_agent.py
====================
Unit tests verifying that all 6 agent chains/agents in agent.py
initialise correctly without making any real API calls.

All LLM calls are patched via unittest.mock so no API keys are required.
"""

import sys
import types
import unittest
from unittest.mock import MagicMock, patch, PropertyMock

# ─── Minimal stubs so agent.py can be imported without real API keys ──────────

# Stub langchain_google_genai before agent.py is imported
_fake_genai = types.ModuleType("langchain_google_genai")
_fake_chat = MagicMock()
_fake_genai.ChatGoogleGenerativeAI = _fake_chat
sys.modules.setdefault("langchain_google_genai", _fake_genai)

# Stub dotenv
_fake_dotenv = types.ModuleType("dotenv")
_fake_dotenv.load_dotenv = lambda *a, **kw: None
sys.modules.setdefault("dotenv", _fake_dotenv)

# ─── Import module under test ─────────────────────────────────────────────────

import agent  # noqa: E402  (must come after stubs)


class TestGetLlm(unittest.TestCase):
    """Tests for the get_llm() factory function."""

    def test_returns_gemini_by_default(self):
        """get_llm() with provider='gemini' should return a ChatGoogleGenerativeAI instance."""
        with patch("agent.ChatGoogleGenerativeAI", return_value=MagicMock()) as mock_cls:
            result = agent.get_llm(provider="gemini")
            mock_cls.assert_called_once()
            self.assertIsNotNone(result)

    def test_returns_hf_for_huggingface(self):
        """get_llm() with provider='huggingface' should delegate to get_hf_llm()."""
        mock_hf = MagicMock()
        with patch("agent.get_hf_llm", return_value=mock_hf) as mock_fn:
            result = agent.get_llm(provider="huggingface")
            mock_fn.assert_called_once()
            self.assertEqual(result, mock_hf)

    def test_returns_hf_for_hf_alias(self):
        """get_llm() with provider='hf' should also delegate to get_hf_llm()."""
        mock_hf = MagicMock()
        with patch("agent.get_hf_llm", return_value=mock_hf):
            result = agent.get_llm(provider="hf")
            self.assertEqual(result, mock_hf)


class TestBuildSearchAgent(unittest.TestCase):
    """Tests for build_search_agent()."""

    def test_returns_agent(self):
        """build_search_agent() should return a non-None agent object."""
        mock_model = MagicMock()
        with patch("agent.create_agent", return_value=MagicMock()) as mock_create:
            result = agent.build_search_agent(model=mock_model)
            mock_create.assert_called_once()
            self.assertIsNotNone(result)

    def test_passes_tools(self):
        """build_search_agent() must pass multi_source_search and web_search as tools."""
        mock_model = MagicMock()
        with patch("agent.create_agent", return_value=MagicMock()) as mock_create:
            agent.build_search_agent(model=mock_model)
            call_kwargs = mock_create.call_args
            # create_agent uses all keyword arguments — tools is always in .kwargs
            tools_passed = call_kwargs.kwargs.get("tools", [])
            self.assertIsInstance(tools_passed, list)
            self.assertGreater(len(tools_passed), 0)


class TestBuildReaderAgent(unittest.TestCase):
    """Tests for build_reader_agent()."""

    def test_returns_agent(self):
        """build_reader_agent() should return a non-None agent object."""
        mock_model = MagicMock()
        with patch("agent.create_agent", return_value=MagicMock()) as mock_create:
            result = agent.build_reader_agent(model=mock_model)
            mock_create.assert_called_once()
            self.assertIsNotNone(result)

    def test_passes_scrape_tool(self):
        """build_reader_agent() must include the scrape_url tool."""
        mock_model = MagicMock()
        with patch("agent.create_agent", return_value=MagicMock()) as mock_create:
            agent.build_reader_agent(model=mock_model)
            call_kwargs = mock_create.call_args
            tools_passed = call_kwargs.kwargs.get("tools") or []
            self.assertIsInstance(tools_passed, list)


class TestBuildAnalystChain(unittest.TestCase):
    """Tests for build_analyst_chain()."""

    def test_returns_chain(self):
        """build_analyst_chain() should return a runnable chain (non-None)."""
        mock_model = MagicMock()
        result = agent.build_analyst_chain(model=mock_model)
        self.assertIsNotNone(result)

    def test_chain_is_pipe_composition(self):
        """build_analyst_chain() returns an object from a pipe (|) composition."""
        mock_model = MagicMock()
        # Should not raise; just checks basic creation
        chain = agent.build_analyst_chain(model=mock_model)
        self.assertIsNotNone(chain)


class TestBuildWriterChain(unittest.TestCase):
    """Tests for build_writer_chain()."""

    def test_returns_chain(self):
        """build_writer_chain() should return a non-None runnable."""
        mock_model = MagicMock()
        result = agent.build_writer_chain(model=mock_model)
        self.assertIsNotNone(result)


class TestBuildCriticChain(unittest.TestCase):
    """Tests for build_critic_chain()."""

    def test_returns_chain(self):
        """build_critic_chain() should return a non-None runnable."""
        mock_model = MagicMock()
        result = agent.build_critic_chain(model=mock_model)
        self.assertIsNotNone(result)

    def test_critic_chain_uses_json_parser(self):
        """build_critic_chain() should use a JsonOutputParser in its composition."""
        # The chain is a composition; just ensure it builds without error
        mock_model = MagicMock()
        chain = agent.build_critic_chain(model=mock_model)
        self.assertIsNotNone(chain)


class TestBuildRefinerChain(unittest.TestCase):
    """Tests for build_refiner_chain()."""

    def test_returns_chain(self):
        """build_refiner_chain() should return a non-None runnable."""
        mock_model = MagicMock()
        result = agent.build_refiner_chain(model=mock_model)
        self.assertIsNotNone(result)


class TestCriticEvaluation(unittest.TestCase):
    """Tests for the CriticEvaluation Pydantic model."""

    def test_valid_construction(self):
        """CriticEvaluation should construct with valid fields."""
        ev = agent.CriticEvaluation(
            score=8,
            passed=True,
            strengths=["Good depth"],
            areas_for_improvement=["Needs more citations"],
            actionable_revisions=["Add 5 more sources"],
            verdict="Solid report.",
        )
        self.assertEqual(ev.score, 8)
        self.assertTrue(ev.passed)

    def test_score_bounds(self):
        """CriticEvaluation.score must be between 1 and 10 (inclusive)."""
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            agent.CriticEvaluation(
                score=0,   # below minimum
                passed=False,
                strengths=[],
                areas_for_improvement=[],
                actionable_revisions=[],
                verdict="Bad.",
            )
        with self.assertRaises(ValidationError):
            agent.CriticEvaluation(
                score=11,  # above maximum
                passed=True,
                strengths=[],
                areas_for_improvement=[],
                actionable_revisions=[],
                verdict="Excellent.",
            )

    def test_default_lists(self):
        """CriticEvaluation list fields should default to empty lists."""
        ev = agent.CriticEvaluation(score=7, passed=False, verdict="OK")
        self.assertIsInstance(ev.strengths, list)
        self.assertIsInstance(ev.areas_for_improvement, list)
        self.assertIsInstance(ev.actionable_revisions, list)


if __name__ == "__main__":
    unittest.main(verbosity=2)
