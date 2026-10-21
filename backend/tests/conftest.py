"""
tests/conftest.py
==================
Shared pytest fixtures and configuration for the Research AI test suite.

Fixtures defined here are automatically available to all test files in
the tests/ directory without explicit imports.
"""

import sys
import os
import types
import pytest
from unittest.mock import MagicMock


# ─── Ensure project root is on sys.path ──────────────────────────────────────

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


# ─── Global stubs (applied before any test module import) ────────────────────
# These prevent ImportError for packages that require real API keys or
# heavy system dependencies during import time.

def _stub_if_missing(name: str, **attrs) -> types.ModuleType:
    if name not in sys.modules:
        mod = types.ModuleType(name)
        mod.__dict__.update(attrs)
        sys.modules[name] = mod
    return sys.modules[name]


try:
    import dotenv
except ImportError:
    _stub_if_missing("dotenv", load_dotenv=lambda *a, **kw: None)
_stub_if_missing("langchain_google_genai", ChatGoogleGenerativeAI=MagicMock())


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def sample_search_result():
    """A valid SearchResult dict for use in tests."""
    return {
        "title": "Sample Research Paper",
        "url": "https://arxiv.org/abs/2301.00001",
        "snippet": "This paper explores the intersection of AI and healthcare.",
        "source_name": "arXiv",
        "source_type": "academic",
    }


@pytest.fixture
def sample_markdown_report():
    """A minimal valid research report Markdown string."""
    return """---
title: "AI in Healthcare"
generated_at: "2024-01-01T12:00:00"
critic_score: 9/10
---

# AI in Healthcare: A Comprehensive Overview

## Executive Summary

Artificial intelligence is transforming modern healthcare delivery.
Sources: https://arxiv.org/abs/2301.00001 and https://en.wikipedia.org/wiki/AI

## Key Findings

- LLMs improve diagnostic accuracy by **34%** in radiology tasks
- Regulatory frameworks are lagging behind technological innovation

## References

- https://pubmed.ncbi.nlm.nih.gov/example
- https://openai.com/research/gpt-4
"""


@pytest.fixture
def minimal_research_state():
    """A minimal valid ResearchState dict for orchestrator tests."""
    return {
        "topic": "AI in Healthcare",
        "provider": "gemini",
        "interactive": False,
        "max_revisions": 1,
        "approval_threshold": 8,
        "output_dir": "reports",
        "research_request": {
            "refined_query": "AI applications in healthcare",
            "sub_queries": ["LLMs in diagnostics", "AI drug discovery"],
            "research_type": "exploratory",
            "focus_areas": ["Clinical AI", "Regulatory"],
            "suggested_source_types": ["academic", "web"],
        },
        "search_results_raw": [
            {
                "title": "AI Diagnostics Study",
                "url": "https://arxiv.org/abs/2301.12345",
                "snippet": "LLMs achieve 94% accuracy in chest X-ray interpretation.",
                "source_name": "arXiv",
                "source_type": "academic",
            }
        ],
        "search_results": "AI Diagnostics Study\nhttps://arxiv.org/abs/2301.12345",
        "top_urls": ["https://arxiv.org/abs/2301.12345"],
        "source_summary": "arXiv: 1 item",
        "scraped_content": "Deep content extracted from arXiv paper.",
        "analysis": "Synthesis: AI demonstrates strong performance in radiology tasks.",
        "report": "# AI in Healthcare\n\nFindings from multiple sources...",
        "critic_score": 9,
        "critic_evaluation": {
            "score": 9,
            "passed": True,
            "strengths": ["Comprehensive", "Well cited"],
            "areas_for_improvement": [],
            "actionable_revisions": [],
            "verdict": "Excellent research report.",
        },
        "critic_feedback": "Score: 9/10 — APPROVED",
        "revision_count": 0,
        "revision_history": [],
        "final_report": "",
        "saved_report_path": "",
        "status": "started",
        "logs": ["Research pipeline initialized"],
        "rag_context": "RAG context: AI diagnostic accuracy data from ChromaDB.",
    }


@pytest.fixture
def mock_llm():
    """A MagicMock that behaves like a LangChain LLM."""
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(content="Mock LLM response")
    return llm
