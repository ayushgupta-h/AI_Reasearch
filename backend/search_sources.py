"""
Multi-Source Search System
===========================
Searches across multiple sources simultaneously and aggregates results:
  - Tavily           (general web — needs TAVILY_API_KEY)
  - arXiv            (academic papers — free, no key)
  - Wikipedia        (encyclopedia — free, no key)
  - DuckDuckGo       (fallback web — free, no key)
  - HuggingFace      (AI/ML papers — needs HF_TOKEN)
  - NewsAPI          (news — optional, needs NEWS_API_KEY)
  - Google Scholar   (academic — optional, needs SERP_API_KEY)

Sources are selected based on the `suggested_source_types` from the
query intake system and deduplicated by URL.
"""

from abc import ABC, abstractmethod
from pydantic import BaseModel, Field
from typing import Optional
import asyncio
from concurrent.futures import ThreadPoolExecutor
import re
import os
import xml.etree.ElementTree as ET
import requests
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

load_dotenv()

console = Console()


# ─── Data Model ──────────────────────────────────────────────────────────────


class SearchResult(BaseModel):
    """A single result from any search source."""
    title: str = Field(description="Title of the result")
    url: str = Field(description="URL of the result")
    snippet: str = Field(description="Text snippet / summary")
    source_name: str = Field(description="Name of the source (e.g. 'arXiv')")
    source_type: str = Field(description="Category (e.g. 'academic', 'web')")


# ─── Abstract Base ───────────────────────────────────────────────────────────


class BaseSearchSource(ABC):
    """Every search source must implement this interface."""
    name: str = "Unknown"
    source_type: str = "web"

    @abstractmethod
    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        """Run a search and return a list of results."""

    def is_available(self) -> bool:
        """Return False if a required API key is missing."""
        return True


# ─── Source Implementations ──────────────────────────────────────────────────


class TavilySearch(BaseSearchSource):
    """General web search via Tavily API."""
    name = "Tavily"
    source_type = "web"

    def __init__(self):
        from tavily import TavilyClient
        api_key = os.getenv("TAVILY_API_KEY")
        self.client = TavilyClient(api_key=api_key) if api_key else None

    def is_available(self) -> bool:
        return self.client is not None

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        try:
            data = self.client.search(query=query, max_results=max_results)
            return [
                SearchResult(
                    title=r.get("title", "Untitled"),
                    url=r.get("url", ""),
                    snippet=r.get("content", "")[:500],
                    source_name=self.name,
                    source_type=self.source_type,
                )
                for r in data.get("results", [])
            ]
        except Exception as e:
            console.print(f"    [dim red]⚠ Tavily search failed: {e}[/dim red]")
            return []


class ArxivSearch(BaseSearchSource):
    """Academic paper search via the arXiv Atom API (free, no key)."""
    name = "arXiv"
    source_type = "academic"

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        try:
            resp = requests.get(
                "http://export.arxiv.org/api/query",
                params={
                    "search_query": f"all:{query}",
                    "start": 0,
                    "max_results": max_results,
                    "sortBy": "relevance",
                    "sortOrder": "descending",
                },
                timeout=15,
            )
            resp.raise_for_status()

            ns = {"atom": "http://www.w3.org/2005/Atom"}
            root = ET.fromstring(resp.text)
            results = []

            for entry in root.findall("atom:entry", ns):
                title_el = entry.find("atom:title", ns)
                summary_el = entry.find("atom:summary", ns)
                id_el = entry.find("atom:id", ns)

                title = (title_el.text or "").strip().replace("\n", " ") if title_el is not None else "Untitled"
                snippet = (summary_el.text or "").strip().replace("\n", " ")[:500] if summary_el is not None else ""
                url = (id_el.text or "").strip() if id_el is not None else ""

                results.append(SearchResult(
                    title=title,
                    url=url,
                    snippet=snippet,
                    source_name=self.name,
                    source_type=self.source_type,
                ))

            return results
        except Exception as e:
            console.print(f"    [dim red]⚠ arXiv search failed: {e}[/dim red]")
            return []


class WikipediaSearch(BaseSearchSource):
    """Encyclopedia search via the MediaWiki API (free, no key)."""
    name = "Wikipedia"
    source_type = "encyclopedia"

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        try:
            resp = requests.get(
                "https://en.wikipedia.org/w/api.php",
                headers={"User-Agent": "ResearchAI/1.0 (https://github.com/ayushgupta-h/AI_Reasearch)"},
                params={
                    "action": "query",
                    "list": "search",
                    "srsearch": query,
                    "srlimit": max_results,
                    "format": "json",
                    "utf8": 1,
                },
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

            results = []
            for item in data.get("query", {}).get("search", []):
                # Strip HTML tags from the snippet
                raw_snippet = item.get("snippet", "")
                clean_snippet = re.sub(r"<[^>]+>", "", raw_snippet)

                page_title = item.get("title", "Untitled")
                page_url = f"https://en.wikipedia.org/wiki/{page_title.replace(' ', '_')}"

                results.append(SearchResult(
                    title=page_title,
                    url=page_url,
                    snippet=clean_snippet[:500],
                    source_name=self.name,
                    source_type=self.source_type,
                ))

            return results
        except Exception as e:
            console.print(f"    [dim red]⚠ Wikipedia search failed: {e}[/dim red]")
            return []


class DuckDuckGoSearch(BaseSearchSource):
    """Fallback web search via DuckDuckGo (free, no key)."""
    name = "DuckDuckGo"
    source_type = "web"

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        try:
            from duckduckgo_search import DDGS
            results = []
            with DDGS() as ddgs:
                for r in ddgs.text(query, max_results=max_results):
                    results.append(SearchResult(
                        title=r.get("title", "Untitled"),
                        url=r.get("href", ""),
                        snippet=r.get("body", "")[:500],
                        source_name=self.name,
                        source_type=self.source_type,
                    ))
            return results
        except ImportError:
            console.print("    [dim red]⚠ duckduckgo-search not installed, skipping[/dim red]")
            return []
        except Exception as e:
            console.print(f"    [dim red]⚠ DuckDuckGo search failed: {e}[/dim red]")
            return []


class NewsAPISearch(BaseSearchSource):
    """News article search via NewsAPI (needs NEWS_API_KEY)."""
    name = "NewsAPI"
    source_type = "news"

    def __init__(self):
        self.api_key = os.getenv("NEWS_API_KEY")

    def is_available(self) -> bool:
        return self.api_key is not None

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        try:
            resp = requests.get(
                "https://newsapi.org/v2/everything",
                params={
                    "q": query,
                    "pageSize": max_results,
                    "sortBy": "relevancy",
                    "language": "en",
                    "apiKey": self.api_key,
                },
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

            return [
                SearchResult(
                    title=a.get("title") or "Untitled",
                    url=a.get("url") or "",
                    snippet=(a.get("description") or "")[:500],
                    source_name=self.name,
                    source_type=self.source_type,
                )
                for a in data.get("articles", [])
            ]
        except Exception as e:
            console.print(f"    [dim red]⚠ NewsAPI search failed: {e}[/dim red]")
            return []


class GoogleScholarSearch(BaseSearchSource):
    """Academic search via SerpAPI's Google Scholar engine (needs SERP_API_KEY)."""
    name = "Google Scholar"
    source_type = "academic"

    def __init__(self):
        self.api_key = os.getenv("SERP_API_KEY")

    def is_available(self) -> bool:
        return self.api_key is not None

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        try:
            resp = requests.get(
                "https://serpapi.com/search",
                params={
                    "engine": "google_scholar",
                    "q": query,
                    "num": max_results,
                    "api_key": self.api_key,
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            return [
                SearchResult(
                    title=r.get("title", "Untitled"),
                    url=r.get("link", ""),
                    snippet=r.get("snippet", "")[:500],
                    source_name=self.name,
                    source_type=self.source_type,
                )
                for r in data.get("organic_results", [])
            ]
        except Exception as e:
            console.print(f"    [dim red]⚠ Google Scholar search failed: {e}[/dim red]")
            return []


class HuggingFaceSearch(BaseSearchSource):
    """Search AI/ML research papers on HuggingFace Hub (needs HF_TOKEN)."""
    name = "HuggingFace Papers"
    source_type = "academic"

    def __init__(self):
        self.api_token = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACEHUB_API_TOKEN")

    def is_available(self) -> bool:
        return self.api_token is not None

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        try:
            headers = {"Authorization": f"Bearer {self.api_token}"}

            # ── Try the HF daily papers / search API ─────────────
            resp = requests.get(
                "https://huggingface.co/api/daily_papers",
                params={"search": query, "limit": max_results},
                headers=headers,
                timeout=10,
            )

            results: list[SearchResult] = []

            if resp.status_code == 200:
                papers = resp.json()
                if isinstance(papers, list):
                    for p in papers[:max_results]:
                        paper = p.get("paper", p) if isinstance(p, dict) else p
                        paper_id = paper.get("id", "")
                        title = paper.get("title", "Untitled")
                        summary = paper.get("summary", "")[:500]
                        url = f"https://huggingface.co/papers/{paper_id}" if paper_id else ""

                        results.append(SearchResult(
                            title=title,
                            url=url,
                            snippet=summary,
                            source_name=self.name,
                            source_type=self.source_type,
                        ))

            # ── Fallback: search HF models for research context ──
            if len(results) < max_results:
                remaining = max_results - len(results)
                model_resp = requests.get(
                    "https://huggingface.co/api/models",
                    params={
                        "search": query,
                        "limit": remaining,
                        "sort": "downloads",
                        "direction": "-1",
                    },
                    headers=headers,
                    timeout=10,
                )
                if model_resp.status_code == 200:
                    for m in model_resp.json()[:remaining]:
                        model_id = m.get("modelId", m.get("id", ""))
                        tags = m.get("tags", [])
                        pipeline_tag = m.get("pipeline_tag", "")
                        desc = (
                            f"Model: {model_id} | "
                            f"Pipeline: {pipeline_tag} | "
                            f"Tags: {', '.join(tags[:5])} | "
                            f"Downloads: {m.get('downloads', 'N/A')}"
                        )
                        results.append(SearchResult(
                            title=model_id,
                            url=f"https://huggingface.co/{model_id}",
                            snippet=desc[:500],
                            source_name=self.name,
                            source_type="technical_docs",
                        ))

            return results
        except Exception as e:
            console.print(f"    [dim red]⚠ HuggingFace search failed: {e}[/dim red]")
            return []


# ─── Source → Covered Types Mapping ──────────────────────────────────────────
#
# Tells the orchestrator which requested source_types each source can satisfy.
# For example, if the intake suggests "academic", the orchestrator will
# activate arXiv, Wikipedia, Google Scholar, and HuggingFace Papers.

SOURCE_COVERAGE: dict[str, list[str]] = {
    "tavily":          ["news", "technical_docs", "government", "industry_reports", "blogs"],
    "arxiv":           ["academic"],
    "wikipedia":       ["academic", "technical_docs"],
    "duckduckgo":      ["news", "technical_docs", "government", "industry_reports", "blogs"],
    "huggingface":     ["academic", "technical_docs"],
    "newsapi":         ["news"],
    "google_scholar":  ["academic"],
}


# ─── Orchestrator ────────────────────────────────────────────────────────────


class SearchOrchestrator:
    """
    Orchestrates searches across all available sources.

    Usage::

        orch = SearchOrchestrator()
        results = orch.search("AI in healthcare", source_types=["academic", "news"])
        print(orch.format_results(results))
    """

    def __init__(self):
        self.sources: dict[str, BaseSearchSource] = {
            "tavily":         TavilySearch(),
            "arxiv":          ArxivSearch(),
            "wikipedia":      WikipediaSearch(),
            "duckduckgo":     DuckDuckGoSearch(),
            "huggingface":    HuggingFaceSearch(),
            "newsapi":        NewsAPISearch(),
            "google_scholar": GoogleScholarSearch(),
        }

    # ── Source selection ─────────────────────────────────────────

    def get_available_sources(self) -> dict[str, BaseSearchSource]:
        """Return only sources whose API keys / deps are present."""
        return {
            name: src for name, src in self.sources.items()
            if src.is_available()
        }

    def select_sources(
        self, requested_types: list[str] | None = None
    ) -> dict[str, BaseSearchSource]:
        """
        Pick the sources that cover the requested source_types.
        Falls back to Tavily + DuckDuckGo if nothing matches.
        """
        available = self.get_available_sources()

        if not requested_types:
            return available

        # Normalise requested types
        requested = [t.lower().strip() for t in requested_types]

        selected: dict[str, BaseSearchSource] = {}
        for name, src in available.items():
            covers = SOURCE_COVERAGE.get(name, [])
            if any(rt in covers for rt in requested):
                selected[name] = src

        # Guarantee at least one web source
        if not selected:
            for fallback in ("tavily", "duckduckgo"):
                if fallback in available:
                    selected[fallback] = available[fallback]
                    break

        return selected

    # ── Core search ──────────────────────────────────────────────

    def search(
        self,
        query: str,
        source_types: list[str] | None = None,
        max_results_per_source: int = 3,
    ) -> list[SearchResult]:
        """
        Search across selected sources and return de-duplicated results.
        """
        sources = self.select_sources(source_types)
        all_results: list[SearchResult] = []
        seen_urls: set[str] = set()

        for name, src in sources.items():
            console.print(f"    [dim]🔎 Searching {src.name}...[/dim]")
            try:
                hits = src.search(query, max_results=max_results_per_source)
            except Exception as exc:
                console.print(f"    [dim red]⚠ {src.name} error: {exc}[/dim red]")
                hits = []

            added = 0
            for r in hits:
                if r.url and r.url not in seen_urls:
                    seen_urls.add(r.url)
                    all_results.append(r)
                    added += 1

            console.print(
                f"    [dim green]   ✓ {added} unique result(s) from {src.name}[/dim green]"
            )

        return all_results

    async def _async_search_source(
        self,
        executor: ThreadPoolExecutor,
        loop: asyncio.AbstractEventLoop,
        src_name: str,
        src: "BaseSearchSource",
        query: str,
        max_results_per_source: int,
    ) -> list[SearchResult]:
        """Run one blocking source.search() call inside a thread-pool executor."""
        console.print(f"    [dim]🔎 [{src_name}] Searching: {query[:60]}...[/dim]")
        try:
            hits = await loop.run_in_executor(
                executor,
                lambda: src.search(query, max_results=max_results_per_source),
            )
            return hits
        except Exception as exc:
            console.print(f"    [dim red]⚠ {src.name} async error for '{query[:40]}': {exc}[/dim red]")
            return []

    async def async_search_all(
        self,
        queries: list[str],
        source_types: list[str] | None = None,
        max_results_per_source: int = 3,
        max_workers: int = 10,
    ) -> list[SearchResult]:
        """
        Execute all (source × sub-query) pairs concurrently using asyncio.gather().

        Args:
            queries: List of sub-queries to search simultaneously.
            source_types: Restrict search to these source types (e.g. ["academic", "web"]).
            max_results_per_source: Max results each source returns per query.
            max_workers: Thread-pool workers for blocking I/O calls.

        Returns:
            De-duplicated list of SearchResult objects, ordered by source then query.
        """
        sources = self.select_sources(source_types)
        loop = asyncio.get_event_loop()

        console.print(
            f"  [cyan]⚡ Async parallel search: {len(queries)} sub-queries × "
            f"{len(sources)} sources = {len(queries) * len(sources)} concurrent tasks[/cyan]"
        )

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            tasks = [
                self._async_search_source(
                    executor, loop, src_name, src, query, max_results_per_source
                )
                for query in queries
                for src_name, src in sources.items()
            ]
            results_nested: list[list[SearchResult]] = await asyncio.gather(*tasks)

        # Flatten and deduplicate by URL
        seen_urls: set[str] = set()
        all_results: list[SearchResult] = []
        for batch in results_nested:
            for r in batch:
                if r.url and r.url not in seen_urls:
                    seen_urls.add(r.url)
                    all_results.append(r)
                elif not r.url and r.snippet:
                    all_results.append(r)

        console.print(
            f"  [green]✓ Async search complete: {len(all_results)} unique results collected[/green]"
        )
        return all_results

    # ── Display helpers ──────────────────────────────────────────

    def display_source_status(self):
        """Print a table showing which sources are available."""
        table = Table(title="🌐 Search Sources", show_lines=True)
        table.add_column("Source", style="bold white")
        table.add_column("Type", style="cyan")
        table.add_column("Status")
        table.add_column("Covers", style="dim")

        for name, src in self.sources.items():
            ok = src.is_available()
            status = (
                "[green]✅ Ready[/green]" if ok
                else "[dim red]❌ Missing API key[/dim red]"
            )
            covers = ", ".join(SOURCE_COVERAGE.get(name, []))
            table.add_row(src.name, src.source_type, status, covers)

        console.print(table)

    def display_search_summary(self, results: list[SearchResult]):
        """Print a summary panel grouping results by source."""
        grouped = self.get_results_by_source(results)

        lines = []
        for source_name, items in grouped.items():
            lines.append(f"  [bold]{source_name}[/bold]: {len(items)} result(s)")

        lines.append(f"\n  [bold green]Total unique results: {len(results)}[/bold green]")

        console.print(Panel(
            "\n".join(lines),
            title="📊 Multi-Source Search Summary",
            style="blue",
        ))

    # ── Formatting utilities ─────────────────────────────────────

    @staticmethod
    def format_results(results: list[SearchResult]) -> str:
        """Format results into a readable string for the LLM."""
        if not results:
            return "No search results found."

        parts: list[str] = []
        for i, r in enumerate(results, 1):
            parts.append(
                f"[{i}] ({r.source_name} — {r.source_type})\n"
                f"Title: {r.title}\n"
                f"URL: {r.url}\n"
                f"Snippet: {r.snippet}"
            )
        return "\n\n-----\n\n".join(parts)

    @staticmethod
    def get_all_urls(results: list[SearchResult]) -> list[str]:
        """Extract all unique URLs from results (preserving order)."""
        return list(dict.fromkeys(r.url for r in results if r.url))

    @staticmethod
    def get_results_by_source(
        results: list[SearchResult],
    ) -> dict[str, list[SearchResult]]:
        """Group results by source name."""
        grouped: dict[str, list[SearchResult]] = {}
        for r in results:
            grouped.setdefault(r.source_name, []).append(r)
        return grouped
