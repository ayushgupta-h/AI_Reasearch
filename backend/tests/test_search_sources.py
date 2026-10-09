"""
tests/test_search_sources.py
==============================
Unit tests for the search_sources.py module.

Tests cover:
  - is_available() for every source (with and without API keys)
  - SearchResult Pydantic model schema validation
  - SearchOrchestrator.select_sources() routing logic
  - SearchOrchestrator.format_results(), get_all_urls(), get_results_by_source()
  - async_search_all() concurrent path (mocked HTTP)

No real HTTP requests are made — all network calls are patched.
"""

import sys
import types
import unittest
from unittest.mock import MagicMock, patch, AsyncMock

# ─── Stub dotenv before import ────────────────────────────────────────────────
_fake_dotenv = types.ModuleType("dotenv")
_fake_dotenv.load_dotenv = lambda *a, **kw: None
sys.modules.setdefault("dotenv", _fake_dotenv)

import search_sources  # noqa: E402
from search_sources import (
    SearchResult,
    TavilySearch,
    ArxivSearch,
    WikipediaSearch,
    DuckDuckGoSearch,
    NewsAPISearch,
    GoogleScholarSearch,
    HuggingFaceSearch,
    SearchOrchestrator,
    SOURCE_COVERAGE,
)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _make_result(
    title: str = "Test Title",
    url: str = "https://example.com/test",
    snippet: str = "Test snippet content.",
    source_name: str = "TestSource",
    source_type: str = "web",
) -> SearchResult:
    return SearchResult(
        title=title,
        url=url,
        snippet=snippet,
        source_name=source_name,
        source_type=source_type,
    )


# ─── Test: SearchResult schema ────────────────────────────────────────────────

class TestSearchResultSchema(unittest.TestCase):
    """Validates the SearchResult Pydantic model schema."""

    def test_valid_construction(self):
        r = _make_result()
        self.assertEqual(r.title, "Test Title")
        self.assertEqual(r.url, "https://example.com/test")
        self.assertEqual(r.source_name, "TestSource")
        self.assertEqual(r.source_type, "web")

    def test_model_dump_produces_dict(self):
        r = _make_result()
        d = r.model_dump()
        self.assertIsInstance(d, dict)
        self.assertIn("title", d)
        self.assertIn("url", d)
        self.assertIn("snippet", d)
        self.assertIn("source_name", d)
        self.assertIn("source_type", d)

    def test_missing_required_field_raises(self):
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            SearchResult(url="https://example.com")  # missing title, snippet, etc.

    def test_snippet_can_be_empty_string(self):
        r = SearchResult(
            title="T", url="https://x.com", snippet="",
            source_name="S", source_type="web"
        )
        self.assertEqual(r.snippet, "")


# ─── Test: Source availability ────────────────────────────────────────────────

class TestSourceAvailability(unittest.TestCase):
    """Tests is_available() for every source class."""

    # ── Always-available sources (no API key required) ──

    def test_arxiv_always_available(self):
        src = ArxivSearch()
        self.assertTrue(src.is_available())

    def test_wikipedia_always_available(self):
        src = WikipediaSearch()
        self.assertTrue(src.is_available())

    def test_duckduckgo_always_available(self):
        src = DuckDuckGoSearch()
        self.assertTrue(src.is_available())

    # ── Sources requiring API keys ──

    def test_tavily_unavailable_without_key(self):
        with patch.dict("os.environ", {}, clear=True):
            with patch("search_sources.TavilySearch.__init__", lambda self: setattr(self, "client", None) or None):
                src = TavilySearch.__new__(TavilySearch)
                src.client = None
                self.assertFalse(src.is_available())

    def test_tavily_available_with_key(self):
        src = TavilySearch.__new__(TavilySearch)
        src.client = MagicMock()  # simulate a valid client
        self.assertTrue(src.is_available())

    def test_newsapi_unavailable_without_key(self):
        with patch.dict("os.environ", {}, clear=True):
            src = NewsAPISearch.__new__(NewsAPISearch)
            src.api_key = None
            self.assertFalse(src.is_available())

    def test_newsapi_available_with_key(self):
        src = NewsAPISearch.__new__(NewsAPISearch)
        src.api_key = "fake_key_12345"
        self.assertTrue(src.is_available())

    def test_google_scholar_unavailable_without_key(self):
        src = GoogleScholarSearch.__new__(GoogleScholarSearch)
        src.api_key = None
        self.assertFalse(src.is_available())

    def test_google_scholar_available_with_key(self):
        src = GoogleScholarSearch.__new__(GoogleScholarSearch)
        src.api_key = "fake_serp_key"
        self.assertTrue(src.is_available())

    def test_huggingface_unavailable_without_token(self):
        src = HuggingFaceSearch.__new__(HuggingFaceSearch)
        src.api_token = None
        self.assertFalse(src.is_available())

    def test_huggingface_available_with_token(self):
        src = HuggingFaceSearch.__new__(HuggingFaceSearch)
        src.api_token = "hf_faketoken"
        self.assertTrue(src.is_available())


# ─── Test: Source results schema ─────────────────────────────────────────────

class TestArxivSearch(unittest.TestCase):
    """Tests ArxivSearch.search() result schema with mocked HTTP."""

    _SAMPLE_ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Test Paper Title</title>
    <summary>This is the abstract of the test paper.</summary>
    <id>https://arxiv.org/abs/2301.00001</id>
  </entry>
</feed>"""

    def test_search_returns_list_of_search_results(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = self._SAMPLE_ATOM
        mock_resp.raise_for_status = MagicMock()

        with patch("search_sources.requests.get", return_value=mock_resp):
            src = ArxivSearch()
            results = src.search("test query", max_results=1)

        self.assertIsInstance(results, list)
        self.assertGreater(len(results), 0)
        r = results[0]
        self.assertIsInstance(r, SearchResult)
        self.assertEqual(r.source_name, "arXiv")
        self.assertEqual(r.source_type, "academic")
        self.assertIn("arxiv.org", r.url)

    def test_search_returns_empty_on_http_error(self):
        with patch("search_sources.requests.get", side_effect=Exception("Network error")):
            src = ArxivSearch()
            results = src.search("test query")
        self.assertEqual(results, [])


class TestWikipediaSearch(unittest.TestCase):
    """Tests WikipediaSearch.search() result schema with mocked HTTP."""

    _SAMPLE_JSON = {
        "query": {
            "search": [
                {
                    "title": "Artificial intelligence",
                    "snippet": "Artificial <span>intelligence</span> is a broad field.",
                }
            ]
        }
    }

    def test_search_returns_list_of_search_results(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = self._SAMPLE_JSON
        mock_resp.raise_for_status = MagicMock()

        with patch("search_sources.requests.get", return_value=mock_resp):
            src = WikipediaSearch()
            results = src.search("artificial intelligence", max_results=1)

        self.assertIsInstance(results, list)
        self.assertGreater(len(results), 0)
        r = results[0]
        self.assertIsInstance(r, SearchResult)
        self.assertEqual(r.source_name, "Wikipedia")
        self.assertIn("wikipedia.org", r.url)
        # HTML tags should be stripped from snippet
        self.assertNotIn("<span>", r.snippet)

    def test_search_returns_empty_on_error(self):
        with patch("search_sources.requests.get", side_effect=Exception("Timeout")):
            src = WikipediaSearch()
            results = src.search("test query")
        self.assertEqual(results, [])


class TestDuckDuckGoSearch(unittest.TestCase):
    """Tests DuckDuckGoSearch.search() result schema."""

    def test_search_returns_list_of_search_results(self):
        mock_ddg_result = {
            "title": "DuckDuckGo Result",
            "href": "https://ddg.example.com/result",
            "body": "Body text from DuckDuckGo result.",
        }
        mock_ddgs = MagicMock()
        mock_ddgs.__enter__ = MagicMock(return_value=mock_ddgs)
        mock_ddgs.__exit__ = MagicMock(return_value=False)
        mock_ddgs.text.return_value = [mock_ddg_result]

        # DDGS is imported inline inside the method, so patch the source module
        with patch("duckduckgo_search.DDGS", return_value=mock_ddgs):
            src = DuckDuckGoSearch()
            results = src.search("test query", max_results=1)

        self.assertIsInstance(results, list)
        self.assertGreater(len(results), 0)
        r = results[0]
        self.assertIsInstance(r, SearchResult)
        self.assertEqual(r.source_name, "DuckDuckGo")
        self.assertEqual(r.url, "https://ddg.example.com/result")

    def test_search_returns_empty_on_import_error(self):
        """Should return [] when DDGS raises any exception (network/import error)."""
        # Patch DDGS at the source to raise on instantiation — tests the except path
        with patch("duckduckgo_search.DDGS", side_effect=Exception("DDGS unavailable")):
            src = DuckDuckGoSearch()
            results = src.search("test")
        self.assertEqual(results, [])



# ─── Test: SearchOrchestrator ─────────────────────────────────────────────────

class TestSearchOrchestrator(unittest.TestCase):
    """Tests SearchOrchestrator routing, formatting, and utility methods."""

    def _make_orchestrator_with_mock_sources(self):
        orc = SearchOrchestrator.__new__(SearchOrchestrator)
        # Create mock sources
        web_src = MagicMock()
        web_src.is_available.return_value = True
        web_src.name = "MockWeb"
        web_src.source_type = "web"

        academic_src = MagicMock()
        academic_src.is_available.return_value = True
        academic_src.name = "MockAcademic"
        academic_src.source_type = "academic"

        unavailable_src = MagicMock()
        unavailable_src.is_available.return_value = False
        unavailable_src.name = "Unavailable"
        unavailable_src.source_type = "news"

        orc.sources = {
            "mockweb": web_src,
            "mockacademic": academic_src,
            "unavailable": unavailable_src,
        }
        return orc

    def test_get_available_sources_excludes_unavailable(self):
        orc = self._make_orchestrator_with_mock_sources()
        available = orc.get_available_sources()
        self.assertIn("mockweb", available)
        self.assertIn("mockacademic", available)
        self.assertNotIn("unavailable", available)

    def test_select_sources_returns_subset_for_type(self):
        """select_sources should filter to sources that cover the requested types."""
        orc = SearchOrchestrator.__new__(SearchOrchestrator)
        # Patch SOURCE_COVERAGE for our mock names
        mock_src = MagicMock()
        mock_src.is_available.return_value = True
        orc.sources = {
            "arxiv": mock_src,
            "duckduckgo": mock_src,
        }
        with patch.dict(search_sources.SOURCE_COVERAGE, {
            "arxiv": ["academic"],
            "duckduckgo": ["web"],
        }):
            selected = orc.select_sources(["academic"])
        self.assertIn("arxiv", selected)
        self.assertNotIn("duckduckgo", selected)

    def test_format_results_returns_string(self):
        results = [_make_result(title="Result 1"), _make_result(title="Result 2", url="https://b.com")]
        formatted = SearchOrchestrator.format_results(results)
        self.assertIsInstance(formatted, str)
        self.assertIn("Result 1", formatted)
        self.assertIn("Result 2", formatted)

    def test_format_results_empty(self):
        formatted = SearchOrchestrator.format_results([])
        self.assertIn("No search results", formatted)

    def test_get_all_urls_deduplication(self):
        results = [
            _make_result(url="https://a.com"),
            _make_result(url="https://b.com"),
            _make_result(url="https://a.com"),  # duplicate
        ]
        urls = SearchOrchestrator.get_all_urls(results)
        self.assertEqual(len(urls), 2)
        self.assertIn("https://a.com", urls)
        self.assertIn("https://b.com", urls)

    def test_get_results_by_source_grouping(self):
        results = [
            _make_result(source_name="arXiv"),
            _make_result(source_name="Wikipedia"),
            _make_result(source_name="arXiv"),
        ]
        grouped = SearchOrchestrator.get_results_by_source(results)
        self.assertIn("arXiv", grouped)
        self.assertIn("Wikipedia", grouped)
        self.assertEqual(len(grouped["arXiv"]), 2)
        self.assertEqual(len(grouped["Wikipedia"]), 1)

    def test_source_coverage_has_all_sources(self):
        """SOURCE_COVERAGE must define coverage for all 7 standard sources."""
        expected_keys = {"tavily", "arxiv", "wikipedia", "duckduckgo",
                         "huggingface", "newsapi", "google_scholar"}
        for key in expected_keys:
            self.assertIn(key, SOURCE_COVERAGE, msg=f"'{key}' missing from SOURCE_COVERAGE")

    def test_source_coverage_values_are_lists_of_strings(self):
        for source_name, types_list in SOURCE_COVERAGE.items():
            self.assertIsInstance(types_list, list, msg=f"{source_name} coverage is not a list")
            for t in types_list:
                self.assertIsInstance(t, str, msg=f"{source_name} coverage item is not a string")


# ─── Test: async_search_all ───────────────────────────────────────────────────

class TestAsyncSearchAll(unittest.IsolatedAsyncioTestCase):
    """Tests the async_search_all() concurrent search method."""

    async def test_async_search_all_returns_deduplicated_results(self):
        """async_search_all() must return a flat deduplicated list of SearchResults."""
        mock_result_a = _make_result(url="https://a.com", source_name="SrcA")
        mock_result_b = _make_result(url="https://b.com", source_name="SrcB")
        # Duplicate of A — should be dropped
        mock_result_dup = _make_result(url="https://a.com", source_name="SrcC")

        mock_src = MagicMock()
        mock_src.is_available.return_value = True
        mock_src.name = "MockSrc"
        mock_src.source_type = "web"
        # Simulate sync search returning different results per call
        call_count = [0]
        def side_effect(query, max_results=3):
            call_count[0] += 1
            if call_count[0] == 1:
                return [mock_result_a, mock_result_b]
            return [mock_result_dup]

        mock_src.search.side_effect = side_effect

        orc = SearchOrchestrator.__new__(SearchOrchestrator)
        orc.sources = {"mockweb": mock_src}

        with patch.dict(search_sources.SOURCE_COVERAGE, {"mockweb": ["web"]}):
            results = await orc.async_search_all(
                queries=["query one", "query two"],
                source_types=["web"],
                max_results_per_source=3,
            )

        urls = [r.url for r in results]
        # https://a.com must appear only once despite being returned twice
        self.assertEqual(urls.count("https://a.com"), 1)
        self.assertIn("https://b.com", urls)

    async def test_async_search_all_empty_queries(self):
        """async_search_all() with empty query list should still return a list."""
        orc = SearchOrchestrator()
        results = await orc.async_search_all(queries=[], source_types=["web"])
        self.assertIsInstance(results, list)


if __name__ == "__main__":
    unittest.main(verbosity=2)
